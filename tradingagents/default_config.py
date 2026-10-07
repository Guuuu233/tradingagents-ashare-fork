import os
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _default_data_cache_dir() -> str:
    """数据缓存默认目录：发布目录之外的稳定状态路径，跨发布保留。

    历史默认 ``tradingagents/dataflows/data_cache`` 位于 ``releases/<sha>``
    内，每次部署清零。现默认 ``${XDG_STATE_HOME}/tradingagents/data_cache``
    （未设 XDG_STATE_HOME 时为 ``~/.local/state/...``），与 SHA/worktree
    无关。可用 ``TA_DATA_CACHE_DIR`` 覆盖，必须是绝对路径且不得落在本
    工作树或任何 ``releases`` 目录内（同 DAV-1412 存档路径约束）。
    """
    configured = os.getenv("TA_DATA_CACHE_DIR", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise ValueError("TA_DATA_CACHE_DIR must be an absolute path")
        path = path.resolve()
        if _SOURCE_ROOT in path.parents or "releases" in path.parts:
            raise ValueError(
                "TA_DATA_CACHE_DIR must not live inside a worktree/release"
            )
        return str(path)
    state_home = Path(
        os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    )
    return str(state_home / "tradingagents" / "data_cache")


DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TA_RESULTS_DIR", "./results"),
    "data_cache_dir": _default_data_cache_dir(),
    # LLM settings
    "llm_provider": os.getenv("TA_LLM_PROVIDER", "openai"),
    "deep_think_llm": os.getenv("TA_LLM_DEEP", "gpt-4o"),
    "quick_think_llm": os.getenv("TA_LLM_QUICK", "gpt-4o-mini"),
    "backend_url": os.getenv("TA_BASE_URL", "https://api.openai.com/v1"),
    "api_key": os.getenv("TA_API_KEY", ""),
    
    # Provider-specific thinking configuration
    "google_thinking_level": None,      # "high", "minimal", etc.
    "openai_reasoning_effort": None,    # "medium", "high", "low"
    
    # Debate and discussion settings
    "max_debate_rounds": int(os.getenv("TA_MAX_DEBATE") or "3"),
    "max_risk_discuss_rounds": int(os.getenv("TA_MAX_RISK") or "3"),
    "max_recur_limit": 100,
    
    # Prompt language control: zh, en, or auto
    "prompt_language": os.getenv("TA_LANGUAGE", "zh"),
    "prompt_language_by_provider": {},
    
    # Provider routing trace logs
    "provider_trace": os.getenv("TA_TRACE", "1").lower() in ("1", "true", "yes", "on"),
    
    # Data vendor configuration
    "investoday_api_key": os.getenv("INVESTODAY_API_KEY", "").strip(),
    "investoday_base_url": (
        os.getenv("INVESTODAY_BASE_URL", "https://data-api.investoday.net/data").strip()
    ),
    "fuyao_api_key": os.getenv("FUYAO_API_KEY", "").strip(),
    "fuyao_base_url": (
        os.getenv("FUYAO_BASE_URL", "https://fuyao.aicubes.cn").strip()
    ),
    "data_vendors": {
        "core_stock_apis": "cn_akshare,cn_baostock,cn_investoday,yfinance,cn_fuyao",
        "technical_indicators": "cn_akshare,cn_baostock,cn_investoday,yfinance",
        "fundamental_data": "tushare,cn_fuyao,cn_akshare,cn_baostock,cn_investoday,yfinance",
        "news_data": "cn_akshare,cn_baostock,cn_investoday,yfinance",
        "realtime_data": "cn_akshare,cn_investoday,cn_fuyao",
        "cn_market_data": "cn_akshare,cn_fuyao",
        "macro_market_data": "tushare,cn_akshare",
    },
    "tool_vendors": {
        # 全市场新闻：Tushare 新闻网关为主、今日投资兜底（DAV-1374）
        "get_global_news": "tushare,cn_investoday",
        "get_zt_pool": "cn_akshare,cn_fuyao",
        "get_lhb_detail": "cn_akshare,cn_fuyao",
        "get_board_fund_flow": "cn_akshare",
        "get_individual_fund_flow": "cn_akshare",
        "get_hot_stocks_xq": "cn_akshare",
        "get_limit_up_ladder": "cn_fuyao",
    },

    # Social data configuration (Task 7 / §7)
    "social": {
        "mode": os.getenv("TA_SOCIAL_MODE", "disabled"),
        "provider": os.getenv("TA_SOCIAL_PROVIDER", "archive_sqlite"),
        "archive_db": os.getenv("TA_SOCIAL_ARCHIVE_DB", "").strip(),
        "platforms": os.getenv("TA_SOCIAL_PLATFORMS", "xhs,dy"),
        "lookback_days": int(os.getenv("TA_SOCIAL_LOOKBACK_DAYS") or "7"),
        "max_posts": int(os.getenv("TA_SOCIAL_MAX_POSTS") or "100"),
        "max_comments": int(os.getenv("TA_SOCIAL_MAX_COMMENTS") or "300"),
        "min_posts": int(os.getenv("TA_SOCIAL_MIN_POSTS") or "3"),
        "min_classified": int(os.getenv("TA_SOCIAL_MIN_CLASSIFIED") or "20"),
        "min_authors": int(os.getenv("TA_SOCIAL_MIN_AUTHORS") or "10"),
        "evidence_limit": int(os.getenv("TA_SOCIAL_EVIDENCE_LIMIT") or "20"),
        "canary_symbols": os.getenv("TA_SOCIAL_CANARY_SYMBOLS", "").strip(),
        "fetch_timeout": int(os.getenv("TA_SOCIAL_FETCH_TIMEOUT") or "5"),
    },
}
