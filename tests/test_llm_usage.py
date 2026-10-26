import threading

from stock_analyzer import llm, llm_usage


def _ok(model="claude-x", usage=None, fell_back=False):
    return llm.LLMResult(text="t", stop_reason="end_turn", error=None, model=model,
                         usage=usage, fell_back=fell_back)


def _err(msg="boom"):
    return llm.LLMResult(text=None, stop_reason=None, error=msg, model="claude-y",
                         usage=None)


def test_record_ok_result():
    llm_usage.record("fast", _ok(usage={"input_tokens": 10, "output_tokens": 4}))
    s = llm_usage.snapshot()["fast"]
    assert s["calls"] == 1 and s["errors"] == 0
    assert s["last_error"] is None and s["last_error_at"] is None
    assert (s["input_tokens"], s["output_tokens"]) == (10, 4)
    assert s["last_model"] == "claude-x" and s["fell_back"] == 0


def test_record_error_result():
    llm_usage.record("deep", _err("APIError: bad"))
    s = llm_usage.snapshot()["deep"]
    assert s["calls"] == 1 and s["errors"] == 1
    assert s["last_error"] == "APIError: bad"
    assert isinstance(s["last_error_at"], str) and "T" in s["last_error_at"]
    assert s["last_model"] == "claude-y"


def test_record_without_usage_adds_no_tokens():
    llm_usage.record("fast", _ok(usage=None))
    llm_usage.record("fast", _ok(usage={"input_tokens": None, "output_tokens": "7"}))
    s = llm_usage.snapshot()["fast"]
    assert s["calls"] == 2 and s["input_tokens"] == 0 and s["output_tokens"] == 0


def test_token_sums_and_fell_back_count():
    for fb in (True, False, True):
        llm_usage.record("extract", _ok(usage={"input_tokens": 5, "output_tokens": 2},
                                        fell_back=fb))
    s = llm_usage.snapshot()["extract"]
    assert (s["input_tokens"], s["output_tokens"], s["fell_back"]) == (15, 6, 2)


def test_snapshot_is_a_copy():
    llm_usage.record("fast", _ok())
    snap = llm_usage.snapshot()
    snap["fast"]["calls"] = 99
    snap["other"] = {}
    assert llm_usage.snapshot()["fast"]["calls"] == 1
    assert "other" not in llm_usage.snapshot()


def test_reset_clears():
    llm_usage.record("fast", _ok())
    llm_usage.reset()
    assert llm_usage.snapshot() == {}


def test_record_never_raises():
    llm_usage.record("fast", None)
    llm_usage.record("fast", object())
    assert llm_usage.snapshot() == {}


def test_concurrent_record_smoke():
    def work():
        for _ in range(100):
            llm_usage.record("fast", _ok(usage={"input_tokens": 1, "output_tokens": 1}))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    s = llm_usage.snapshot()["fast"]
    assert s["calls"] == 800 and s["input_tokens"] == 800
