"""Storage layout helpers for ``result_data``.

DAV-1506 (存储治理 B-1, D-072 附注): the unique physical layout is
``result_data.short_term`` / ``result_data.medium_term`` with each slice's own
``market_data_context``. ``horizons.<h>`` and the top-level
``market_data_context`` are no longer persisted — they are rebuilt on demand
by :mod:`tradingagents.storage.result_data_compat`.
"""
