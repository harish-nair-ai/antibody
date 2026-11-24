def total(items):  # type: ignore[no-untyped-def]
    return sum(item.price for item in items)  # noqa: E501
