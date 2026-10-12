"""Social data test fixtures and mock MediaCrawler database generators (Task 3 / B3).

Specifications:
- docs/social_data/implementation_plan.md §3.2, §3.3, §4.1, §4.2, Task 3
- work/2026-08-27-unified-final-plan.md Phase 8 / B3
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List, Optional, Union


# MediaCrawler standard table DDLs (commit d6f7c5bb906b6dac40ddf343ef9e26438a3de092)

MEDIACRAWLER_XHS_NOTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS xhs_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    nickname TEXT,
    avatar TEXT,
    ip_location TEXT,
    note_id TEXT NOT NULL,
    type TEXT,
    title TEXT,
    desc TEXT,
    video_url TEXT,
    time INTEGER,
    last_update_time INTEGER,
    liked_count TEXT,
    collected_count TEXT,
    comment_count TEXT,
    share_count TEXT,
    image_list TEXT,
    tag_list TEXT,
    note_url TEXT,
    source_keyword TEXT,
    xsec_token TEXT,
    add_ts INTEGER,
    last_modify_ts INTEGER
);
"""

MEDIACRAWLER_XHS_NOTE_COMMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS xhs_note_comment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    nickname TEXT,
    avatar TEXT,
    ip_location TEXT,
    comment_id TEXT NOT NULL,
    note_id TEXT NOT NULL,
    content TEXT,
    create_time INTEGER,
    like_count TEXT,
    sub_comment_count TEXT,
    parent_comment_id TEXT,
    last_modify_ts INTEGER,
    add_ts INTEGER
);
"""

MEDIACRAWLER_DOUYIN_AWEME_SCHEMA = """
CREATE TABLE IF NOT EXISTS douyin_aweme (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    sec_uid TEXT,
    short_user_id TEXT,
    user_unique_id TEXT,
    nickname TEXT,
    avatar TEXT,
    user_signature TEXT,
    ip_location TEXT,
    aweme_id TEXT NOT NULL,
    aweme_type TEXT,
    title TEXT,
    desc TEXT,
    create_time INTEGER,
    liked_count TEXT,
    comment_count TEXT,
    share_count TEXT,
    collected_count TEXT,
    aweme_url TEXT,
    source_keyword TEXT,
    add_ts INTEGER,
    last_modify_ts INTEGER
);
"""

MEDIACRAWLER_DOUYIN_AWEME_COMMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS douyin_aweme_comment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comment_id TEXT NOT NULL,
    aweme_id TEXT NOT NULL,
    user_id TEXT,
    sec_uid TEXT,
    nickname TEXT,
    avatar_url TEXT,
    ip_location TEXT,
    content TEXT,
    create_time INTEGER,
    like_count TEXT,
    reply_comment_total TEXT,
    parent_comment_id TEXT,
    last_modify_ts INTEGER,
    add_ts INTEGER
);
"""


def init_mediacrawler_db(db: Union[sqlite3.Connection, str]) -> sqlite3.Connection:
    """Initialize a mock MediaCrawler SQLite database with all 4 standard tables."""
    if isinstance(db, str):
        conn = sqlite3.connect(db)
    else:
        conn = db

    conn.executescript(
        MEDIACRAWLER_XHS_NOTE_SCHEMA
        + MEDIACRAWLER_XHS_NOTE_COMMENT_SCHEMA
        + MEDIACRAWLER_DOUYIN_AWEME_SCHEMA
        + MEDIACRAWLER_DOUYIN_AWEME_COMMENT_SCHEMA
    )
    conn.commit()
    return conn


def populate_sample_mediacrawler_data(conn: sqlite3.Connection) -> None:
    """Populate sample valid and edge-case records into mock MediaCrawler DB."""
    cursor = conn.cursor()

    # 1. Standard XHS Note (with ms timestamps)
    # Published: 2026-08-26T03:12:11Z (1787713931000 ms)
    # Source Updated: 2026-08-26T03:40:00Z (1787715600000 ms)
    # First Seen: 2026-08-26T04:00:02Z (1787716802000 ms)
    # Snapshot At: 2026-08-26T06:10:00Z (1787724600000 ms)
    cursor.execute(
        """
        INSERT INTO xhs_note (
            user_id, nickname, avatar, ip_location, note_id, type, title, desc,
            video_url, time, last_update_time, liked_count, collected_count,
            comment_count, share_count, image_list, tag_list, note_url,
            source_keyword, xsec_token, add_ts, last_modify_ts
        ) VALUES (
            'xhs_user_001', '小红书老股民', 'https://avatar/1.png', '北京', 'note_65abc01', 'normal',
            '寒武纪深度解析与展望', '今日寒武纪放量突破，主力资金净流入明显。',
            NULL, 1787713931000, 1787715600000, '123', '20', '45', '5',
            'https://img/1.png,https://img/2.png', '#寒武纪#A股',
            'https://www.xiaohongshu.com/explore/note_65abc01?xsec_token=AB12345&xsec_source=pc_share',
            '寒武纪', 'AB12345', 1787716802000, 1787724600000
        )
        """
    )

    # 2. XHS Note with Empty Desc (allowed for attention metrics)
    cursor.execute(
        """
        INSERT INTO xhs_note (
            user_id, nickname, avatar, ip_location, note_id, type, title, desc,
            video_url, time, last_update_time, liked_count, collected_count,
            comment_count, share_count, image_list, tag_list, note_url,
            source_keyword, xsec_token, add_ts, last_modify_ts
        ) VALUES (
            'xhs_user_002', '看图说话', 'https://avatar/2.png', '上海', 'note_65abc02', 'normal',
            '寒武纪走势图', '',
            NULL, 1787714000000, 0, '10', '2', '1', '0',
            'https://img/3.png', '#寒武纪',
            'https://www.xiaohongshu.com/explore/note_65abc02',
            '寒武纪', 'CD67890', 1787716805000, 1787724610000
        )
        """
    )

    # 3. Invalid XHS Note (missing published_at / time=0 -> must be rejected)
    cursor.execute(
        """
        INSERT INTO xhs_note (
            user_id, nickname, avatar, ip_location, note_id, type, title, desc,
            video_url, time, last_update_time, liked_count, collected_count,
            comment_count, share_count, image_list, tag_list, note_url,
            source_keyword, xsec_token, add_ts, last_modify_ts
        ) VALUES (
            'xhs_user_bad', '测试异常', '', '', 'note_bad_time', 'normal',
            '缺发布时间的笔记', '正文内容',
            NULL, 0, 0, '0', '0', '0', '0',
            '', '', 'https://www.xiaohongshu.com/explore/note_bad_time',
            '寒武纪', '', 1787716805000, 1787724610000
        )
        """
    )

    # 4. Standard XHS Comment (with seconds timestamps)
    cursor.execute(
        """
        INSERT INTO xhs_note_comment (
            user_id, nickname, avatar, ip_location, comment_id, note_id, content,
            create_time, like_count, sub_comment_count, parent_comment_id,
            last_modify_ts, add_ts
        ) VALUES (
            'xhs_commenter_01', '热心股友', 'https://avatar/c1.png', '广东', 'comment_xhs_001',
            'note_65abc01', '今天我也建仓了，看好后市！',
            1787714500, '15', '2', NULL,
            1787724700, 1787717000
        )
        """
    )

    # 5. Standard Douyin Aweme (with seconds timestamps)
    cursor.execute(
        """
        INSERT INTO douyin_aweme (
            user_id, sec_uid, short_user_id, user_unique_id, nickname, avatar,
            user_signature, ip_location, aweme_id, aweme_type, title, desc,
            create_time, liked_count, comment_count, share_count, collected_count,
            aweme_url, source_keyword, add_ts, last_modify_ts
        ) VALUES (
            'dy_user_001', 'MS4wLjABAAAA_sec123', '123456', 'dy_unique_1', '短视频股评', 'https://avatar/dy1.png',
            '每天聊股票', '浙江', 'aweme_789001', 'video', '芯片半导体龙头分析',
            '寒武纪今日放量大涨，逻辑在算力需求爆发。',
            1787713900, '500', '88', '30', '120',
            'https://www.douyin.com/video/aweme_789001?utm_source=copy',
            '寒武纪', 1787716800, 1787724600
        )
        """
    )

    # 6. Standard Douyin Comment
    cursor.execute(
        """
        INSERT INTO douyin_aweme_comment (
            comment_id, aweme_id, user_id, sec_uid, nickname, avatar_url, ip_location,
            content, create_time, like_count, reply_comment_total, parent_comment_id,
            last_modify_ts, add_ts
        ) VALUES (
            'dy_comment_001', 'aweme_789001', 'dy_user_c1', 'MS4wLjABAAAA_c1', '抖音老韭菜',
            'https://avatar/dyc1.png', '江苏', '算力是核心方向，跟进！',
            1787714200, '25', '0', NULL,
            1787724650, 1787716900
        )
        """
    )

    conn.commit()


# ---------------------------------------------------------------------------
# Anonymized MediaCrawler schema (DAV-1462 requirement 1)
#
# Mirrors the local sandbox pin d6f7c5bb (mediacrawler/database/models.py:38,60,
# 76,90,113,129): the teaching/privacy build carries `creator_hash` and has NO
# `user_id` / `sec_uid` anywhere. The fixture reproduces exactly that column set
# so the importer's author-column mapping and its schema guard are exercised
# against the real production shape, not the upstream one.
# ---------------------------------------------------------------------------

MEDIACRAWLER_ANON_XHS_NOTE_SCHEMA = """
CREATE TABLE IF NOT EXISTS xhs_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_hash TEXT,
    nickname TEXT,
    add_ts INTEGER,
    last_modify_ts INTEGER,
    note_id TEXT NOT NULL,
    type TEXT,
    title TEXT,
    desc TEXT,
    time INTEGER,
    last_update_time INTEGER,
    liked_count TEXT,
    collected_count TEXT,
    comment_count TEXT,
    share_count TEXT,
    note_url TEXT,
    source_keyword TEXT,
    xsec_token TEXT
);
"""

MEDIACRAWLER_ANON_XHS_NOTE_COMMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS xhs_note_comment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_hash TEXT,
    nickname TEXT,
    add_ts INTEGER,
    last_modify_ts INTEGER,
    comment_id TEXT NOT NULL,
    create_time INTEGER,
    note_id TEXT NOT NULL,
    content TEXT,
    sub_comment_count TEXT,
    parent_comment_id TEXT,
    like_count TEXT
);
"""

MEDIACRAWLER_ANON_DOUYIN_AWEME_SCHEMA = """
CREATE TABLE IF NOT EXISTS douyin_aweme (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_hash TEXT,
    nickname TEXT,
    add_ts INTEGER,
    last_modify_ts INTEGER,
    aweme_id TEXT NOT NULL,
    title TEXT,
    desc TEXT,
    create_time INTEGER,
    liked_count TEXT,
    comment_count TEXT,
    share_count TEXT,
    collected_count TEXT,
    aweme_url TEXT,
    source_keyword TEXT
);
"""

MEDIACRAWLER_ANON_DOUYIN_AWEME_COMMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS douyin_aweme_comment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    creator_hash TEXT,
    nickname TEXT,
    add_ts INTEGER,
    last_modify_ts INTEGER,
    comment_id TEXT NOT NULL,
    create_time INTEGER,
    aweme_id TEXT NOT NULL,
    content TEXT,
    sub_comment_count TEXT,
    parent_comment_id TEXT,
    like_count TEXT
);
"""

MEDIACRAWLER_ANON_SCHEMAS = (
    MEDIACRAWLER_ANON_XHS_NOTE_SCHEMA
    + MEDIACRAWLER_ANON_XHS_NOTE_COMMENT_SCHEMA
    + MEDIACRAWLER_ANON_DOUYIN_AWEME_SCHEMA
    + MEDIACRAWLER_ANON_DOUYIN_AWEME_COMMENT_SCHEMA
)


def init_anonymized_mediacrawler_db(db: Union[sqlite3.Connection, str]) -> sqlite3.Connection:
    """Initialize a mock anonymized (creator_hash-only) MediaCrawler database."""
    if isinstance(db, str):
        conn = sqlite3.connect(db)
    else:
        conn = db
    conn.executescript(MEDIACRAWLER_ANON_SCHEMAS)
    conn.commit()
    return conn


def populate_sample_anonymized_mediacrawler_data(conn: sqlite3.Connection) -> None:
    """Populate the anonymized schema with rows carrying only creator_hash."""
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO xhs_note (
            creator_hash, nickname, note_id, type, title, desc,
            time, last_update_time, liked_count, collected_count,
            comment_count, share_count, note_url, source_keyword,
            add_ts, last_modify_ts
        ) VALUES (
            'a1b2c3d4e5f60718', '匿***星', 'anon_note_01', 'normal',
            '阳光电源今日大涨', '光伏板块情绪回暖，阳光电源逆变器出货超预期。',
            1787713931000, 1787715600000, '88', '12', '9', '3',
            'https://www.xiaohongshu.com/explore/anon_note_01',
            '阳光电源', 1787716802000, 1787724600000
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO xhs_note (
            creator_hash, nickname, note_id, type, title, desc,
            time, last_update_time, liked_count, collected_count,
            comment_count, share_count, note_url, source_keyword,
            add_ts, last_modify_ts
        ) VALUES (
            'b2c3d4e5f6071829', '热***友', 'anon_note_02', 'normal',
            '中国移动分红', '中国移动公告年度分红方案。',
            1787714000000, 0, '10', '2', '1', '0',
            'https://www.xiaohongshu.com/explore/anon_note_02',
            '中国移动', 1787716805000, 1787724610000
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO xhs_note_comment (
            creator_hash, nickname, add_ts, comment_id, create_time, note_id, content,
            sub_comment_count, parent_comment_id, like_count, last_modify_ts
        ) VALUES (
            'c3d4e5f607182930', '股***吧', 1787717000, 'anon_comment_01', 1787714500,
            'anon_note_01', '跟一手，突破确认了。',
            '0', NULL, '15', 1787724700
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO douyin_aweme (
            creator_hash, nickname, aweme_id, title, desc, create_time,
            liked_count, comment_count, share_count, collected_count,
            aweme_url, source_keyword, add_ts, last_modify_ts
        ) VALUES (
            'd4e5f60718293041', '短***评', 'anon_aweme_01', '煤炭板块复盘',
            '陕西煤业今日动力煤价格企稳。',
            1787713900, '500', '88', '30', '120',
            'https://www.douyin.com/video/anon_aweme_01',
            '陕西煤业', 1787716800, 1787724600
        )
        """
    )
    cursor.execute(
        """
        INSERT INTO douyin_aweme_comment (
            creator_hash, nickname, add_ts, comment_id, create_time, aweme_id, content,
            sub_comment_count, parent_comment_id, like_count, last_modify_ts
        ) VALUES (
            'e5f6071829304152', '抖***民', 1787716900, 'anon_comment_02', 1787714200,
            'anon_aweme_01', '高股息防守品种，跌不动。',
            '0', NULL, '25', 1787724650
        )
        """
    )
    conn.commit()


def populate_creator_hash_blank_rows(conn: sqlite3.Connection) -> None:
    """Populate rows whose creator_hash is present but empty/blank.

    The schema guard passes (the column exists), so this exercises the per-row
    path: no author value available -> no author_id_hash.
    """
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO xhs_note (
            creator_hash, nickname, note_id, type, title, desc,
            time, last_update_time, liked_count, collected_count,
            comment_count, share_count, note_url, source_keyword,
            add_ts, last_modify_ts
        ) VALUES (
            '   ', '空***名', 'anon_note_blank', 'normal',
            '无作者行', '正文内容存在但作者列是空白。',
            1787713931000, 0, '5', '1', '0', '0',
            'https://www.xiaohongshu.com/explore/anon_note_blank',
            '', 1787716802000, 1787724600000
        )
        """
    )
    conn.commit()
