"""端到端测试：POST /corpus/preview 登记预演。

覆盖：纯废止 / 跨文档跨条号修订 / 分支歧义 / 固定引用的前后对比，
无残留（成功与失败均不落库）、之后可正常登记、重复版本 409、
非法关系 422、草案自带引用不参与比较、变化列表按引用 ID 升序。
"""


# --------------------------------------------------------------------------- #
# 测试数据构造辅助
# --------------------------------------------------------------------------- #
def _register_old_law(client, expires_at=None):
    """旧条例 2000 版：第一条，2000-01-01 生效；expires_at 可省略。"""
    clause = {"clause_id": "ART-1", "title": "第一条",
              "effective_at": "2000-01-01T00:00:00Z"}
    if expires_at is not None:
        clause["expires_at"] = expires_at
    r = client.post("/corpus", json={
        "doc_id": "OLD-LAW", "version": "2000",
        "name": "旧条例",
        "effective_at": "2000-01-01T00:00:00Z",
        "clauses": [clause],
    })
    assert r.status_code == 201, r.text


def _register_citing_doc(client, doc_id="JUDICIAL-DOC", target_version=None):
    """登记一份引用 OLD-LAW#ART-1 的司法文件。"""
    r = client.post("/corpus", json={
        "doc_id": doc_id, "version": "2021",
        "effective_at": "2021-01-01T00:00:00Z",
        "clauses": [{"clause_id": "ART-9",
                     "effective_at": "2021-01-01T00:00:00Z"}],
        "citations": [{
            "source_clause": "ART-9",
            "target_doc": "OLD-LAW",
            "target_clause": "ART-1",
            "target_version": target_version,
        }],
    })
    assert r.status_code == 201, r.text


def _repeal_draft():
    """拟登记的废止令：2020-06-01 生效，纯废止旧条例第一条（无接续）。"""
    return {
        "doc_id": "REPEAL-ACT", "version": "2020",
        "name": "废止令",
        "effective_at": "2020-06-01T00:00:00Z",
        "clauses": [{"clause_id": "ART-1",
                     "effective_at": "2020-06-01T00:00:00Z"}],
        "revisions": [{
            "kind": "repeals",
            "predecessor_doc": "OLD-LAW",
            "predecessor_version": "2000",
            "predecessor_clause": "ART-1",
            "note": "本法生效之日起废止旧条例",
        }],
    }


# --------------------------------------------------------------------------- #
# 前后对比
# --------------------------------------------------------------------------- #
def test_preview_terminal_repeal_change_and_no_residue(client):
    """纯废止草案：before 有效 / after 已废止；预演不留痕迹且可正常登记。"""
    _register_old_law(client)
    _register_citing_doc(client)

    r = client.post("/corpus/preview", json={
        "document": _repeal_draft(),
        "target_at": "2021-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc_id"] == "REPEAL-ACT"
    assert body["version"] == "2020"
    assert body["target_at"] == "2021-01-01T00:00:00+00:00"
    assert body["total_citations"] == 1
    assert body["changed"] == 1

    change = body["changes"][0]
    assert change["before"]["status"] == "effective"
    assert change["before"]["resolved"] == {
        "doc_id": "OLD-LAW", "version": "2000", "clause_id": "ART-1",
    }
    assert change["after"]["status"] == "repealed"
    assert change["after"]["resolved"] is None
    # after 链路末步为草案登记的纯废止终止点（沿用 /audit 判定）
    term = change["after"]["chains"][0]["steps"][-1]
    assert term["terminal"] is True
    assert term["edge_in"] == "repeals"
    assert term["state_at_target"] == "repealed"
    assert term["effective_at"] == "2020-06-01T00:00:00+00:00"
    assert term["recorded_by"] == {"doc_id": "REPEAL-ACT", "version": "2020"}

    # 预演不留痕迹：草案版本不存在，库中引用数与审查结论不变
    assert client.get("/corpus/REPEAL-ACT/2020").status_code == 404
    audit = client.post(
        "/audit", json={"target_at": "2021-01-01T00:00:00Z"}
    ).json()
    assert audit["total"] == 1
    assert audit["results"][0]["status"] == "effective"

    # 之后仍可正常登记这份版本，且结果与预演的 after 一致
    r = client.post("/corpus", json=_repeal_draft())
    assert r.status_code == 201, r.text
    audit = client.post(
        "/audit", json={"target_at": "2021-01-01T00:00:00Z"}
    ).json()
    assert audit["results"][0]["status"] == "repealed"


def test_preview_cross_doc_revision_changes_resolved(client):
    """跨文档、跨条号的 revises：已失效引用被新法接续“复活”。"""
    # 旧条例 2021-01-01 失效，与新法生效时间衔接
    _register_old_law(client, expires_at="2021-01-01T00:00:00Z")
    _register_citing_doc(client)
    draft = {
        "doc_id": "NEW-LAW", "version": "2021",
        "effective_at": "2021-01-01T00:00:00Z",
        "clauses": [{"clause_id": "ART-99",
                     "effective_at": "2021-01-01T00:00:00Z"}],
        "revisions": [{
            "kind": "revises",
            "predecessor_doc": "OLD-LAW", "predecessor_version": "2000",
            "predecessor_clause": "ART-1",
            "successor_doc": "NEW-LAW", "successor_version": "2021",
            "successor_clause": "ART-99",
            "note": "旧条例并入新法，条号变更",
        }],
    }
    r = client.post("/corpus/preview", json={
        "document": draft, "target_at": "2022-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == 1
    change = body["changes"][0]
    # before：旧版已过失效时间，无接续 -> 已废止
    assert change["before"]["status"] == "repealed"
    assert change["before"]["resolved"] is None
    # after：沿 revises 链解析到新法（跨文档、跨条号）
    assert change["after"]["status"] == "effective"
    assert change["after"]["resolved"] == {
        "doc_id": "NEW-LAW", "version": "2021", "clause_id": "ART-99",
    }
    # after 链路延伸至新法节点
    steps = change["after"]["chains"][0]["steps"]
    assert steps[-1]["node"]["doc_id"] == "NEW-LAW"
    assert steps[-1]["edge_in"] == "revises"


def test_preview_branch_introduces_ambiguity(client):
    """草案引入第二个接续分支：before 有效 / after 歧义。"""
    # 旧条例 2021-01-01 失效，与分支生效时间衔接：before 唯一有效
    _register_old_law(client, expires_at="2021-01-01T00:00:00Z")
    _register_citing_doc(client)
    client.post("/corpus", json={
        "doc_id": "BRANCH-A", "version": "2021",
        "effective_at": "2021-01-01T00:00:00Z",
        "clauses": [{"clause_id": "ART-1",
                     "effective_at": "2021-01-01T00:00:00Z"}],
        "revisions": [{
            "kind": "revises",
            "predecessor_doc": "OLD-LAW", "predecessor_version": "2000",
            "predecessor_clause": "ART-1",
            "successor_doc": "BRANCH-A", "successor_version": "2021",
            "successor_clause": "ART-1",
        }],
    })
    draft = {
        "doc_id": "BRANCH-B", "version": "2021",
        "effective_at": "2021-01-01T00:00:00Z",
        "clauses": [{"clause_id": "ART-1",
                     "effective_at": "2021-01-01T00:00:00Z"}],
        "revisions": [{
            "kind": "revises",
            "predecessor_doc": "OLD-LAW", "predecessor_version": "2000",
            "predecessor_clause": "ART-1",
            "successor_doc": "BRANCH-B", "successor_version": "2021",
            "successor_clause": "ART-1",
        }],
    }
    r = client.post("/corpus/preview", json={
        "document": draft, "target_at": "2022-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    change = r.json()["changes"][0]
    assert change["before"]["status"] == "effective"
    assert change["before"]["resolved"]["doc_id"] == "BRANCH-A"
    assert change["after"]["status"] == "ambiguous"
    assert len(change["after"]["effective_candidates"]) == 2
    assert len(change["after"]["chains"]) == 2


def test_preview_pinned_citation_follows_audit_rules(client):
    """固定版本引用：达到草案废止时刻后同样判定为已废止。"""
    _register_old_law(client)
    _register_citing_doc(client, target_version="2000")

    r = client.post("/corpus/preview", json={
        "document": _repeal_draft(),
        "target_at": "2021-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    change = r.json()["changes"][0]
    assert change["before"]["pinned"] is True
    assert change["before"]["status"] == "effective"
    assert change["after"]["pinned"] is True
    assert change["after"]["status"] == "repealed"

    # 废止时刻之前的目标日：结论不变，但链路多出草案引入的废止
    # 终止步（尚未生效），链路变化同样会被列出
    r = client.post("/corpus/preview", json={
        "document": _repeal_draft(),
        "target_at": "2020-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] == 1
    change = body["changes"][0]
    assert change["before"]["status"] == "effective"
    assert change["after"]["status"] == "effective"
    assert len(change["before"]["chains"][0]["steps"]) == 1
    term = change["after"]["chains"][0]["steps"][-1]
    assert term["terminal"] is True
    assert term["state_at_target"] == "not_yet_effective"


def test_preview_unrelated_draft_no_changes(client):
    """与存量引用无关的草案：变化列表为空。"""
    _register_old_law(client)
    _register_citing_doc(client)
    draft = {
        "doc_id": "STANDALONE", "version": "v1",
        "effective_at": "2021-01-01T00:00:00Z",
        "clauses": [{"clause_id": "A",
                     "effective_at": "2021-01-01T00:00:00Z"}],
    }
    r = client.post("/corpus/preview", json={
        "document": draft, "target_at": "2022-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_citations"] == 1
    assert body["changed"] == 0
    assert body["changes"] == []
    # 无残留
    assert client.get("/corpus/STANDALONE/v1").status_code == 404


def test_preview_draft_own_citations_excluded(client):
    """拟登记版本自带的引用不参与比较，也不留在库中。"""
    _register_old_law(client)
    _register_citing_doc(client)
    draft = _repeal_draft()
    draft["citations"] = [{
        "source_clause": "ART-1",
        "target_doc": "OLD-LAW",
        "target_clause": "ART-1",
    }]
    r = client.post("/corpus/preview", json={
        "document": draft, "target_at": "2021-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    # 仅库中原有的 1 条引用参与比较
    assert body["total_citations"] == 1
    assert body["changed"] == 1
    sources = {c["before"]["source"]["doc_id"] for c in body["changes"]}
    assert sources == {"JUDICIAL-DOC"}
    # 预演后库中仍只有原来的 1 条引用
    audit = client.post("/audit", json={}).json()
    assert audit["total"] == 1


def test_preview_changes_sorted_by_citation_id(client):
    """多条引用：仅列出受影响的，按原引用 ID 升序。"""
    _register_old_law(client)
    _register_citing_doc(client, doc_id="CITING-1")          # id 1：受影响
    # id 2：指向不存在目标，草案不影响其 not_found 结论
    client.post("/corpus", json={
        "doc_id": "OTHER-DOC", "version": "2021",
        "effective_at": "2021-01-01T00:00:00Z",
        "clauses": [{"clause_id": "A",
                     "effective_at": "2021-01-01T00:00:00Z"}],
        "citations": [{"source_clause": "A",
                       "target_doc": "GHOST", "target_clause": "X"}],
    })
    _register_citing_doc(client, doc_id="CITING-2")          # id 3：受影响

    r = client.post("/corpus/preview", json={
        "document": _repeal_draft(),
        "target_at": "2021-01-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_citations"] == 3
    ids = [c["citation_id"] for c in body["changes"]]
    assert ids == sorted(ids)
    assert len(ids) == 2
    sources = {c["before"]["source"]["doc_id"] for c in body["changes"]}
    assert sources == {"CITING-1", "CITING-2"}


def test_preview_default_target_at_is_now(client):
    """target_at 缺省为当前 UTC 时间。"""
    _register_old_law(client)
    _register_citing_doc(client)
    r = client.post("/corpus/preview", json={"document": _repeal_draft()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_at"].endswith("+00:00")
    # 当前时刻远晚于草案废止时刻：结论应为已废止
    assert body["changes"][0]["after"]["status"] == "repealed"


# --------------------------------------------------------------------------- #
# 错误响应与 /corpus 一致
# --------------------------------------------------------------------------- #
def test_preview_duplicate_version_conflict(client):
    """拟登记版本已存在：与 /corpus 相同的 409。"""
    _register_old_law(client)
    r = client.post("/corpus/preview", json={
        "document": {
            "doc_id": "OLD-LAW", "version": "2000",
            "effective_at": "2000-01-01T00:00:00Z",
            "clauses": [],
        },
        "target_at": "2021-01-01T00:00:00Z",
    })
    assert r.status_code == 409
    assert "不得重复写入" in r.json()["detail"]


def test_preview_invalid_revision_422_and_no_residue(client):
    """非法修订关系：与 /corpus 相同的 422；失败同样不留痕迹。"""
    _register_old_law(client)
    draft = {
        "doc_id": "BAD-ACT", "version": "v1",
        "effective_at": "2021-01-01T00:00:00Z",
        "clauses": [{"clause_id": "A",
                     "effective_at": "2021-01-01T00:00:00Z"}],
        "revisions": [{
            "kind": "revises",
            "predecessor_doc": "OLD-LAW",
            "predecessor_version": "2000",
            "predecessor_clause": "ART-1",
            # revises 缺 successor：与 /corpus 相同的校验错误
        }],
    }
    r = client.post("/corpus/preview", json={"document": draft})
    assert r.status_code == 422
    assert "successor" in r.json()["detail"]

    # 失败的预演不留任何数据，修正后可正常登记
    assert client.get("/corpus/BAD-ACT/v1").status_code == 404
    draft["revisions"][0].update({
        "successor_doc": "BAD-ACT", "successor_version": "v1",
        "successor_clause": "A",
    })
    r = client.post("/corpus", json=draft)
    assert r.status_code == 201, r.text


def test_preview_dangling_revision_warns_but_succeeds(client):
    """前序节点暂缺：与 /corpus 一样仅告警，预演成功且不留痕迹。"""
    _register_old_law(client)
    _register_citing_doc(client)
    draft = {
        "doc_id": "FUTURE-LAW", "version": "2030",
        "effective_at": "2030-01-01T00:00:00Z",
        "clauses": [{"clause_id": "A",
                     "effective_at": "2030-01-01T00:00:00Z"}],
        "revisions": [{
            "kind": "revises",
            "predecessor_doc": "MISSING-LAW",
            "predecessor_version": "2029",
            "predecessor_clause": "A1",
            "successor_doc": "FUTURE-LAW",
            "successor_version": "2030",
            "successor_clause": "A",
        }],
    }
    r = client.post("/corpus/preview", json={
        "document": draft, "target_at": "2030-06-01T00:00:00Z",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert any("前序节点不存在" in w for w in body["warnings"])
    assert body["changed"] == 0
    assert client.get("/corpus/FUTURE-LAW/2030").status_code == 404
