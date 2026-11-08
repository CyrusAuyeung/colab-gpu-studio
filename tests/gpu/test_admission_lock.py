import threading

from app.db import Store


def test_admission_waits_for_short_transaction(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'lock.db'))
    held = threading.Event()
    release = threading.Event()

    def transaction():
        with store.admission_lock() as acquired:
            assert acquired
            held.set()
            release.wait(2)

    worker = threading.Thread(target=transaction)
    worker.start()
    assert held.wait(2)
    timer = threading.Timer(0.05, release.set)
    timer.start()
    try:
        with store.admission_lock(timeout=1) as acquired:
            assert acquired
    finally:
        release.set()
        worker.join(2)
        timer.join(2)


def test_admission_wait_is_bounded(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'lock.db'))
    held = threading.Event()
    release = threading.Event()

    def transaction():
        with store.admission_lock() as acquired:
            assert acquired
            held.set()
            release.wait(2)

    worker = threading.Thread(target=transaction)
    worker.start()
    assert held.wait(2)
    try:
        with store.admission_lock(timeout=0) as admitted:
            assert not admitted
    finally:
        release.set()
        worker.join(2)
