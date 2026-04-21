import datetime
import requests
import time
import re
import os
import sys
import threading
import webbrowser

# API端点URL，用于获取B站用户信息
URL = "https://uapis.cn/api/v1/social/bilibili/userinfo"
# 用户名验证正则：仅允许字母和数字组成
pattern = re.compile(r'^[A-Za-z0-9]+$')

# 全局状态变量
paused = False  # 暂停标志
results = []    # 存储查询到的有效结果
index = 1       # 结果序号计数器

def is_valid_name(name):
    """
    验证用户名是否符合有效格式
    
    Args:
        name: 待验证的用户名字符串
        
    Returns:
        bool: 是否为有效的用户名
            1. 必须是字符串类型
            2. 必须仅包含字母和数字（由pattern正则定义）
    """
    return isinstance(name, str) and bool(pattern.fullmatch(name))

def fetch_name(uid, max_retries=3, retry_delay=2):
    """
    通过UID获取B站用户名称，带重试机制处理服务器错误
    
    Args:
        uid: B站用户ID
        max_retries: 最大重试次数
        retry_delay: 重试前等待的秒数
        
    Returns:
        str|None: 
            - 用户名字符串（如果成功获取）
            - "RATE_LIMIT" 表示达到API请求频率限制
            - "SERVER_ERROR" 表示服务器或API响应失败（即使重试后）
            - None 表示其他未知错误
    """
    retries = 0
    while retries <= max_retries:
        try:
            resp = requests.get(URL, params={"uid": uid}, timeout=10)
            
            # 处理429限流
            if resp.status_code == 429:
                return "RATE_LIMIT"
            
            # 处理5xx服务器错误
            if 500 <= resp.status_code < 600:
                retries += 1
                if retries > max_retries:
                    return "SERVER_ERROR"
                print(f"\n[WARNING] UID {uid} 服务器或API响应失败 (状态码: {resp.status_code})，{retry_delay}秒后重试 ({retries}/{max_retries})")
                time.sleep(retry_delay)
                continue
            
            resp.raise_for_status()  # 检查其他HTTP错误
            data = resp.json()
            # 处理可能的不同API响应结构
            if isinstance(data, dict):
                return data.get("name") or data.get("data", {}).get("name")
            return None
            
        except Exception as e:
            retries += 1
            if retries > max_retries:
                print(f"\n[ERROR] UID {uid} 请求异常 (达到最大重试次数): {e}")
                return "SERVER_ERROR"
            print(f"\n[WARNING] UID {uid} 请求异常: {e}，{retry_delay}秒后重试 ({retries}/{max_retries})")
            time.sleep(retry_delay)
    
    return "SERVER_ERROR"

def input_listener():
    """
    输入监听线程函数，处理用户交互命令
    
    支持命令:
        - "pause": 暂停UID遍历过程
        - "continue": 继续UID遍历过程
        - 数字: 打开对应序号的B站用户空间
    """
    global paused
    while True:
        cmd = input().strip()

        if cmd.lower() == "pause":
            paused = True
            print("\n[PAUSED] 已暂停")

        elif cmd.lower() == "continue":
            paused = False
            print("\n[RESUMED] 继续")

        elif cmd.isdigit():
            idx = int(cmd) - 1
            if 0 <= idx < len(results):
                uid = results[idx][0]
                webbrowser.open(f"https://space.bilibili.com/{uid}")
            else:
                print("无效序号")

def parse_custom_range(input_str):
    """
    解析任意位置变量的范围字符串，支持单双引号
    
    支持格式：
        - "10"114514-"99"114514 或 '10'114514-'99'114514 (变量在开头)
        - 114"514"114-114"999"114 或 114'514'114-114'999'114 (变量在中间)
        - 114514"10"-114514"99" 或 114514'10'-114514'99' (变量在结尾)
    
    返回: (fixed_parts, start_var, end_var)
        fixed_parts: 固定部分的元组 (前缀, 后缀)
        start_var/end_var: 变量部分的起始/结束值
    
    Raises:
        ValueError: 当输入格式不正确时抛出异常
    """
    if '-' not in input_str:
        raise ValueError("输入必须包含 '-' 分隔符")
    
    start_str, end_str = [s.strip() for s in input_str.split('-', 1)]
    
    # 提取变量部分（单双引号内的内容）
    def extract_var(s):
        # 修改正则表达式，同时匹配单引号和双引号
        match = re.search(r'["\'](.*?)["\']', s)
        if not match:
            # 无引号时视为整个字符串是变量
            return '', s, ''
        var = match.group(1)
        prefix = s[:match.start()]
        suffix = s[match.end():]
        return prefix, var, suffix
    
    start_prefix, start_var, start_suffix = extract_var(start_str)
    end_prefix, end_var, end_suffix = extract_var(end_str)
    
    # 验证固定部分一致性
    if start_prefix != end_prefix or start_suffix != end_suffix:
        raise ValueError("固定部分必须一致，请检查引号位置")
    
    try:
        start_var = int(start_var)
        end_var = int(end_var)
    except ValueError:
        raise ValueError("变量部分必须是整数")
    
    if start_var > end_var:
        raise ValueError("起始变量值不能大于结束变量值")
    
    return (start_prefix, start_suffix), start_var, end_var

def main():
    """
    主程序函数，执行以下流程：
    1. 获取用户输入的UID范围
    2. 启动输入监听线程
    3. 遍历指定范围内的UID
    4. 查询有效用户名并保存结果
    """
    global paused, index

    try:
        raw_input = input("请输入UID范围 (例: 114514-1919810 或 114\"514\"114-114\"999\"114): ").strip()
        (prefix, suffix), start_var, end_var = parse_custom_range(raw_input)
    except Exception as e:
        print(f"输入错误: {e}")
        return

    # 请求间隔时间，避免触发API限流（不建议太短）
    delay = 0.1

    # exe / py 兼容路径处理
    if getattr(sys, 'frozen', False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))

    # 使用固定文件名，以追加模式打开
    output_path = os.path.join(base_dir, "results.txt")
    
    # 记录开始时间
    start_time = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    
    # 启动输入监听线程（后台）
    threading.Thread(target=input_listener, daemon=True).start()

    # 以追加模式打开文件
    with open(output_path, "a", encoding="utf-8") as f:
        # 写入开始时间戳
        f.write(f"\n{start_time}（开始时间）\n")
        f.flush()
        
        try:
            total = end_var - start_var + 1
            processed = 0
            
            for var in range(start_var, end_var + 1):
                processed += 1
                # 动态显示当前遍历进度
                progress = f"进度: {processed}/{total} ({processed/total:.1%}) | "
                # 构建完整UID字符串
                uid_str = f"{prefix}{var}{suffix}"
                
                if not uid_str.isdigit():
                    continue
                
                uid = int(uid_str)
                # 实时显示当前正在遍历的UID
                print(f"{progress}正在遍历: {uid}", end='\r', flush=True)
                
                # 暂停时等待
                while paused:
                    time.sleep(0.1)

                result = fetch_name(uid, max_retries=3, retry_delay=2)

                # 处理API限流
                if result == "RATE_LIMIT":
                    print(f"\n[ALERT] 风控触发，停止")
                    break
                
                # 处理服务器错误
                elif result == "SERVER_ERROR":
                    # 记录到文件但不显示在控制台（避免干扰）
                    f.write(f"[{index}] {uid} -> 服务器或API响应失败\n")
                    f.flush()
                    continue
                
                # 有效用户名处理
                elif is_valid_name(result):
                    # 清除进度行后再打印结果
                    print(" " * 80, end='\r', flush=True)
                    line = f"[{index}] {uid} -> {result}"
                    print(line)

                    results.append((uid, result))
                    
                    # 写入格式修改为: 序号-UID-用户名
                    result_line = f"{index}-{uid}-{result}"
                    f.write(result_line + "\n")
                    f.flush()

                    index += 1

                time.sleep(delay)

        except KeyboardInterrupt:
            print("\n中断")
        
        # 所有结果写入完成后，添加空行以便与下次结果分隔
        f.write("\n")
        f.flush()

    print(f"\n已追加保存到: {output_path}")
    input("\n按回车退出...")

if __name__ == "__main__":
    main()