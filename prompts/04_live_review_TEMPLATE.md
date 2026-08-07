THIS TEMPLATE IS NOT AUTHORIZATION TO TRADE.

Use only after the strategy has passed the documented shadow and out-of-sample gates and the MCP configuration has been
separately changed to expose order tools.

Prepare, but do not place, one long-option order. Call review_option_order and display:
- account last four digits
- option symbol, underlying, call/put, strike, expiration
- quantity
- live bid, ask, mark, quote timestamp
- order type and time in force
- proposed limit
- total debit and maximum premium loss
- break-even
- exact strategy evidence and invalidation
- confirmation that there are no open option positions and no prior entry today

Do not place an order unless the user then supplies an exact, fresh approval token containing the option symbol, quantity,
limit price, and a nonce generated in this review. A generic yes/approve is insufficient.
