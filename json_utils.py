"""线程安全的 JSON 文件原子读写工具

背景：早期版本用固定临时文件路径(path + '.tmp')做原子写，多线程并发写时
互相抢同一个 tmp：先 os.replace 的线程会把别人写到一半的内容 rename 成
主文件(写坏缓存)，后到的线程报 Errno 2。本模块用每调用唯一的临时文件
(tempfile.mkstemp) + 全局写锁解决：主文件永远只会是某个写者的完整快照。
"""
import json
import os
import tempfile
import threading

# 全局写锁：串行化所有 JSON 持久化写入，配合唯一 tmp 彻底消除并发撕裂
_writes_lock = threading.Lock()


def atomic_write_json(path: str, data, indent: int = 2) -> None:
    """原子写入 JSON：唯一临时文件 + fsync + os.replace，多线程并发安全。

    - 临时文件每次用 tempfile.mkstemp 生成唯一名，并发写者互不干扰
    - fsync 落盘后 os.replace 原子替换，进程被杀/断电也不会留下半个文件
    - 写锁串行化，保证最后完成 replace 的写者持有完整数据快照
    """
    directory = os.path.dirname(path) or '.'
    os.makedirs(directory, exist_ok=True)
    with _writes_lock:
        fd, tmp = tempfile.mkstemp(
            dir=directory, prefix=os.path.basename(path) + '.', suffix='.tmp'
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            # 写入过程中异常(如磁盘满)时清理残留 tmp；正常路径 tmp 已被 replace 走
            try:
                os.remove(tmp)
            except OSError:
                pass


def backup_corrupt_file(path: str) -> None:
    """把损坏的 JSON 文件备份为 .corrupt 后缀，避免覆盖原始坏文件。

    并发下多个线程同时检测到损坏时，只有第一个 replace 成功，其余静默忽略。
    """
    try:
        os.replace(path, path + '.corrupt')
    except OSError:
        pass