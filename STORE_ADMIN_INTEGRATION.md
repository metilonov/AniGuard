# AniGuard Store and Admin integration

This build connects the Mini App store features to persistent server-side logic.

## Added server modules

- `app/store.py` — AniCoin invoices, Premium cases, advertising orders, support chat and owner endpoints.
- `app/store_rules.py` — pricing rules and Premium case probability table.

## User endpoints

- `GET /api/store/wallet`
- `POST /api/store/coins/invoice`
- `POST /api/store/cases/premium/open`
- `GET /api/store/cases/history`
- `GET /api/store/audience`
- `GET /api/store/ads`
- `POST /api/store/ads`
- `POST /api/store/ads/{order_id}/invoice`
- `GET /api/support/thread`
- `POST /api/support/messages`

## Owner endpoints

- `GET /api/admin/store/overview`
- `GET /api/admin/store/ads`
- `POST /api/admin/store/ads/{order_id}/moderate`
- `GET /api/admin/store/cases`
- `GET /api/admin/store/wallet-transactions`
- `GET /api/admin/support/tickets`
- `POST /api/admin/support/tickets/{ticket_id}/reply`
- `POST /api/admin/support/tickets/{ticket_id}/status`
- `POST /api/admin/store/advertising-settings`

## Payment flow

Telegram Stars invoices are created on the server. The bot validates `pre_checkout_query` and only credits AniCoin or marks an advertising order paid after Telegram sends `successful_payment`.

## Case security

The reward is generated on the server. The browser receives only the finished result and uses it for the horizontal animation. An `Idempotency-Key` prevents duplicate case charges.

## Routes

- `/panel` — group management Mini App
- `/shop` — opens the store view
- `/account` — opens the user profile view
- `/group` — opens the group profile view
- `/admin` — owner panel
