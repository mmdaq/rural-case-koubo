"""人民法院案例库全量采集脚本

用法: python main.py bulk-harvest [--pages N] [--keywords K1,K2,...]
"""
import sys
import time
import json
import argparse

sys.path.insert(0, '.')
from collector.rmfyalk_bulk import (
    harvest_by_keyword, harvest_all_keywords, generate_case_table,
    SEARCH_KEYWORDS, BATCH_SIZE, POLL_INTERVAL, MAX_PAGES_PER_KEYWORD,
)
from collector.extrastore import ExtraStore
from utils.logger import get_logger

log = get_logger("bulk_harvest")


def main():
    parser = argparse.ArgumentParser(description="人民法院案例库批量采集")
    parser.add_argument("--pages", type=int, default=3, help="每关键词最大翻页数（默认3）")
    parser.add_argument("--keywords", type=str, default=None,
                        help="指定关键词，逗号分隔（默认全部）")
    parser.add_argument("--no-details", action="store_true", default=True,
                        help="跳过内容接口（只取搜索摘要，更快且避免限流）")
    parser.add_argument("--with-details", action="store_true",
                        help="启用内容接口补充详情（慢，每案例约6秒）")
    parser.add_argument("--output-table", type=str, default="data/output/案例库案例汇总.md",
                        help="生成表格输出路径")
    args = parser.parse_args()

    extra = ExtraStore('data/extra_cases.json')
    existing_codes = set(extra.data.get("cases", {}).keys())
    log.info("当前案例池: %d 个案例", len(existing_codes))

    # 确定要搜索的关键词
    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    else:
        keywords = SEARCH_KEYWORDS

    log.info("关键词数量: %d, 每批大小: %d, 间隔: %ds, 最大页数: %d",
             len(keywords), BATCH_SIZE, POLL_INTERVAL, args.pages)
    log.info("预计总请求数: ~%d, 预计耗时: ~%.0f 分钟",
             len(keywords) * args.pages, len(keywords) * args.pages * BATCH_SIZE * POLL_INTERVAL / 60)

    # 采集
    results = {}
    for i, kw in enumerate(keywords):
        log.info("[%d/%d] 搜索关键词: %s", i + 1, len(keywords), kw)
        t0 = time.time()
        count = harvest_by_keyword(extra, kw, max_pages=args.pages, fetch_details=args.with_details)
        elapsed = time.time() - t0
        results[kw] = {"new": count, "elapsed": elapsed}
        log.info("  完成: +%d 个新案例, 耗时 %.1fs", count, elapsed)
        # 关键词间等待
        if i < len(keywords) - 1:
            time.sleep(3)

    # 统计
    total_new = sum(r["new"] for r in results.values())
    total_time = sum(r["elapsed"] for r in results.values())
    log.info("=" * 50)
    log.info("采集完成！总耗时: %.0f 分钟", total_time / 60)
    log.info("新增案例: %d 个", total_new)
    log.info("当前池子: %d 个案例", len(extra.data.get("cases", {})))

    # 生成表格
    try:
        generate_case_table(extra, args.output_table)
        log.info("表格已生成: %s", args.output_table)
    except Exception as e:
        log.warning("表格生成失败: %s", e)

    # 输出统计摘要
    summary = {
        "total_new": total_new,
        "total_pool": len(extra.data.get("cases", {})),
        "elapsed_minutes": total_time / 60,
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
