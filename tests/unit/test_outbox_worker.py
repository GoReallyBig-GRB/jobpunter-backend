from app.workers.outbox import run_worker


class FakeDispatcher:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = []

    def dispatch_batch(self, limit=25):
        self.calls.append(limit)
        return next(self.results)


def result(claimed=0):
    return {"claimed": claimed, "processed": claimed, "retrying": 0, "dead": 0, "other": 0}


def test_once_dispatches_one_batch():
    dispatcher = FakeDispatcher([result(2), result()])
    batches = run_worker(dispatcher, once=True, batch_size=7)
    assert batches == 1
    assert dispatcher.calls == [7]


def test_worker_sleeps_only_when_no_events_claimed():
    dispatcher = FakeDispatcher([result(), result(1)])
    sleeps = []
    stopped = iter([False, False, True])
    batches = run_worker(
        dispatcher,
        interval_seconds=0.5,
        should_stop=lambda: next(stopped),
        sleep=sleeps.append,
    )
    assert batches == 2
    assert sleeps == [0.5]


def test_worker_rejects_invalid_batch_size():
    dispatcher = FakeDispatcher([result()])
    try:
        run_worker(dispatcher, batch_size=0, once=True)
    except ValueError as exc:
        assert "batch_size" in str(exc)
    else:
        raise AssertionError("expected invalid batch size to fail")
