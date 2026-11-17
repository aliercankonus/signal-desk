from signal_desk.collector.order_book_state import L2BookState


def test_trim_keeps_nearest_levels_and_top_of_book():
    book = L2BookState(bids={str(100 - i): "1" for i in range(50)},
                       asks={str(101 + i): "1" for i in range(50)})
    top = book.top_of_book()
    book.trim(10)
    assert len(book.bids) == len(book.asks) == 10
    assert min(map(float, book.bids)) == 91 and max(map(float, book.asks)) == 110
    assert book.top_of_book() == top
    book.trim(10)  # no-op when within bound
    assert len(book.bids) == 10
