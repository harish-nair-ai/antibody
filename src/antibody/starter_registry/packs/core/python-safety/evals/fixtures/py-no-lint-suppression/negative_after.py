def total(line_items):
    # Sum of item prices.
    return sum(item.price for item in line_items)
