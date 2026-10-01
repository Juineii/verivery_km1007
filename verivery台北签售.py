import requests
import time
import os
import subprocess
import csv
import threading
from datetime import datetime

# ================== 配置 ==================
CSV_FILENAME = "VERIVERY台北签售.csv"
GITHUB_REPO = "Juineii/irene_km0518"
GITHUB_BRANCH = "main"

FETCH_INTERVAL = 10      # 爬取间隔（秒）
PUSH_INTERVAL = 60       # 推送检查间隔（秒）

# 全局锁和计数器
lines_since_last_push = 0
lines_lock = threading.Lock()
file_lock = threading.Lock()

URL_TAIWAN = "https://www.kmonstar.com.tw/products/%E6%87%89%E5%8B%9F-261108-verivery-8th-mini-album-confetti-%E5%B0%88%E8%BC%AF%E7%99%BC%E8%A1%8C%E7%B4%80%E5%BF%B5%E7%B0%BD%E5%90%8D%E6%9C%83-in-taipei.json"
URL_INTERNATIONAL = "https://kmonstar.com/api/v1/event/detail/0a9b3cbc-c694-4555-b631-4889d54dfbd1"

HEADERS_TAIWAN = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.kmonstar.com.tw/",
    "Origin": "https://www.kmonstar.com.tw"
}

HEADERS_INTERNATIONAL = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://kmonstar.org/zh/eventproductdetail/873a6d5e-92af-42e4-8bf4-68eb824d9cdb",
    "Origin": "https://kmonstar.org",
    "Cookie": "nation=KR"
}

# ================== 带重试的库存获取函数 ==================
def get_stock_with_retry(url, headers=None, retries=3, timeout=30):
    for i in range(retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if i == retries - 1:
                print(f"❌ 获取失败 (url={url}): {e}")
                return None
            time.sleep(2)
    return None

def get_stock_taiwan():
    data = get_stock_with_retry(URL_TAIWAN, headers=HEADERS_TAIWAN)
    if data:
        try:
            return data['variants'][0]['inventory_quantity']
        except (KeyError, IndexError, TypeError) as e:
            print(f"❌ 解析台湾地址库存失败: {e}")
            return None
    return None

def get_stock_international():
    data = get_stock_with_retry(URL_INTERNATIONAL, headers=HEADERS_INTERNATIONAL)
    if data:
        try:
            return data['data']['optionList'][0]['stockKo']['quantity']
        except (KeyError, IndexError, TypeError) as e:
            print(f"❌ 解析国际地址库存失败: {e}")
            return None
    return None

# ================== Git 推送函数 ==================
def git_push_update():
    try:
        token = os.environ.get('GITHUB_TOKEN')
        if not token:
            print("⚠️ 环境变量 GITHUB_TOKEN 未设置，跳过 Git 推送")
            return False

        remote_url = f"https://{token}@github.com/{GITHUB_REPO}.git"

        subprocess.run(['git', 'add', CSV_FILENAME], check=True, capture_output=True, timeout=30)
        result = subprocess.run(['git', 'diff', '--cached', '--quiet'], capture_output=True, timeout=30)
        if result.returncode != 0:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_msg = f"自动更新数据 {timestamp}"
            subprocess.run(['git', 'commit', '-m', commit_msg], check=True, capture_output=True, timeout=30)
            subprocess.run(
                ['git', 'push', remote_url, f'HEAD:{GITHUB_BRANCH}'],
                check=True, capture_output=True, text=True, timeout=30
            )
            print(f"✅ 已推送到 GitHub: {commit_msg}")
            return True
        else:
            print("⏭️ CSV 文件无变化，跳过推送")
            return True

    except subprocess.TimeoutExpired:
        print("❌ Git 操作超时 (30秒)，推送失败")
        return False
    except subprocess.CalledProcessError as e:
        print(f"❌ Git 操作失败: {e.stderr if e.stderr else e}")
        return False
    except Exception as e:
        print(f"❌ 推送过程中发生错误: {e}")
        return False

# ================== CSV 写入函数 ==================
def save_to_csv(data_rows):
    global lines_since_last_push
    if not data_rows:
        return True

    fieldnames = ["时间", "商品名称", "库存变化", "单笔销量"]
    file_exists = os.path.exists(CSV_FILENAME)

    try:
        with file_lock:
            with open(CSV_FILENAME, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                if not file_exists:
                    writer.writeheader()
                for row in data_rows:
                    row_dict = {
                        "时间": row[0],
                        "商品名称": row[1],
                        "库存变化": row[2],
                        "单笔销量": row[3]
                    }
                    writer.writerow(row_dict)

        for row in data_rows:
            print(f"{row[0]} - 商品名称: {row[1]}, 库存变化: {row[2]}, 单笔销量: {row[3]}")

        with lines_lock:
            lines_since_last_push += len(data_rows)
        return True

    except Exception as e:
        print(f"❌ 写入CSV文件失败: {e}")
        return False

# ================== 推送线程 ==================
def push_worker():
    global lines_since_last_push
    while True:
        time.sleep(PUSH_INTERVAL)
        with lines_lock:
            pending = lines_since_last_push
        if pending > 0:
            print(f"⏰ 定时推送：有 {pending} 条新数据待推送")
            with file_lock:
                success = git_push_update()
            if success:
                with lines_lock:
                    lines_since_last_push = 0
                print("✅ 推送成功，计数器已归零")
            else:
                print("⚠️ 推送失败，下次再试")

# ================== 爬取线程（正确记录台湾地址变化） ==================
def fetch_worker():
    taiwan_previous = None      # 台湾地址上一库存值
    international_previous = None  # 国际地址上一库存值

    while True:
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        data_rows = []

        # 台湾地址
        stock_tw = get_stock_taiwan()
        if stock_tw is not None:
            if taiwan_previous is None:
                # 首次记录初始库存（单笔销量为0）
                data_rows.append([current_time, '台湾地址', f"初始库存: {stock_tw}", abs(stock_tw)])
                taiwan_previous = stock_tw
            elif stock_tw != taiwan_previous:
                diff = taiwan_previous - stock_tw   # 减少量为正
                data_rows.append([current_time, '台湾地址', f"{taiwan_previous} -> {stock_tw}", diff])
                taiwan_previous = stock_tw

        # 国际地址
        stock_int = get_stock_international()
        if stock_int is not None:
            if international_previous is None:
                data_rows.append([current_time, '国际地址', f"初始库存: {stock_int}", 0])
                international_previous = stock_int
            elif stock_int != international_previous:
                diff = international_previous - stock_int
                data_rows.append([current_time, '国际地址', f"{international_previous} -> {stock_int}", diff])
                international_previous = stock_int

        if data_rows:
            save_to_csv(data_rows)

        time.sleep(FETCH_INTERVAL)

# ================== 启动 ==================
if __name__ == "__main__":
    push_thread = threading.Thread(target=push_worker, daemon=True)
    push_thread.start()
    try:
        fetch_worker()
    except KeyboardInterrupt:
        print("\n监控程序被用户终止")
        with lines_lock:
            pending = lines_since_last_push
        if pending > 0:
            print(f"正在推送剩余的 {pending} 条数据...")
            with file_lock:
                success = git_push_update()
            if success:
                print("✅ 剩余数据已推送")
            else:
                print("⚠️ 剩余数据推送失败，请手动检查")
        else:
            print("无待推送数据")