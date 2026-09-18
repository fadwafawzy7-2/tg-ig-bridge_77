from app.dashboard.callback_data import encode, decode

def test_callback_roundtrip():
    data = encode("product", 123)
    assert data == "d9:product:123"
    assert decode(data) == ("product", "123")

def test_invalid_callback():
    assert decode("x:product:1") is None
