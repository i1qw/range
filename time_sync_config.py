import time
import requests
import logging
from threading import Thread
from datetime import datetime

# 配置日志（简化格式）
handler = logging.StreamHandler()
formatter = logging.Formatter('[时间同步] %(message)s')
handler.setFormatter(formatter)
logger = logging.getLogger('TimeSyncManager')
logger.addHandler(handler)
logger.setLevel(logging.INFO)

class TimeSyncManager:
    """时间同步管理器，用于同步本地时间与币安服务器时间"""
    
    def __init__(self, sync_interval=1800, max_allowed_offset=10000):
        """
        初始化时间同步管理器
        :param sync_interval: 同步间隔(秒)，默认1小时同步一次
        :param max_allowed_offset: 最大允许的时间偏移量(毫秒)
        """
        self.sync_interval = sync_interval
        self.max_allowed_offset = max_allowed_offset
        self.time_offset = 0  # 时间偏移量(毫秒)
        self.last_server_time = 0  # 上次同步时的服务器时间
        self.last_local_time = 0  # 上次同步时的本地时间
        self.is_synced = False  # 是否已同步
        self.sync_thread = None  # 同步线程
        self.running = False  # 运行状态
        self.last_sync_time = 0
        self.min_sync_interval = 5
    
    def start(self):
        """启动自动时间同步"""
        if self.running:
            return
            
        self.running = True
        # 立即进行一次同步
        self.sync_time()
        # 启动自动同步线程
        self.sync_thread = Thread(target=self._auto_sync)
        self.sync_thread.daemon = True
        self.sync_thread.start()
    
    def stop(self):
        """停止自动时间同步"""
        self.running = False
        if self.sync_thread and self.sync_thread.is_alive():
            self.sync_thread.join(2.0)
    
    def _auto_sync(self):
        """自动同步时间的内部方法"""
        while self.running:
            try:
                time.sleep(self.sync_interval)
                self.sync_time()
            except Exception as e:
                logger.error(f"自动同步时间时发生错误: {str(e)}")
    
    def sync_time(self, force=False):
        """手动同步时间"""
        current_time = time.time()
        if not force and current_time - self.last_sync_time < self.min_sync_interval:
            return
        try:
            local_request_time = int(current_time * 1000)
            endpoints = [
                'https://api.binance.com/api/v3/time',
                'https://api1.binance.com/api/v3/time',
                'https://api2.binance.com/api/v3/time',
                'https://api3.binance.com/api/v3/time'
            ]
            binance_time = 0
            network_delay = 1000
            for endpoint in endpoints:
                try:
                    request_start = int(time.time() * 1000)
                    response = requests.get(endpoint, timeout=5)
                    response.raise_for_status()
                    request_end = int(time.time() * 1000)
                    binance_time = response.json().get('serverTime', 0)
                    network_delay = request_end - request_start
                    if network_delay < 500:
                        break
                except:
                    continue
            if binance_time == 0:
                raise Exception("所有币安API端点都无法访问")
            local_response_time = int(time.time() * 1000)
            estimated_server_time_at_request = local_request_time + (network_delay // 2)
            self.time_offset = binance_time - estimated_server_time_at_request
            self.last_server_time = binance_time
            self.last_local_time = local_response_time
            self.last_sync_time = current_time
            self.is_synced = True
            abs_offset = abs(self.time_offset)
            status_msg = "成功" if abs_offset <= 100 else "警告"
            if abs_offset <= 100:
                logger.info(f"时间同步{status_msg} (偏移: {self.time_offset:+}ms, 延迟: {network_delay}ms)")
            else:
                logger.warning(f"时间同步{status_msg} (偏移: {self.time_offset:+}ms, 延迟: {network_delay}ms)")
            return True
        except requests.RequestException as e:
            logger.error(f"同步时间时发生网络错误: {str(e)}")
            self.is_synced = False
            return False
        except Exception as e:
            logger.error(f"同步时间时发生未知错误: {str(e)}")
            self.is_synced = False
            return False
    
    def get_synced_timestamp(self):
        """获取同步后的时间戳(毫秒)"""
        if not self.is_synced:
            self.sync_time()
        current_local_time = int(time.time() * 1000)
        if current_local_time - self.last_local_time > self.sync_interval * 1000:
            logger.debug("距离上次同步超过设定间隔，重新同步")
            self.sync_time()
            current_local_time = int(time.time() * 1000)
        if self.last_server_time > 0:
            time_since_last_sync = current_local_time - self.last_local_time
            return self.last_server_time + time_since_last_sync
        else:
            return current_local_time + self.time_offset
    
    def get_synced_timestamp_without_correction(self):
        """获取未经校正的同步时间戳（仅用于调试）"""
        if not self.is_synced:
            self.sync_time()
        
        current_local_time = int(time.time() * 1000)
        time_since_last_sync = current_local_time - self.last_local_time
        return self.last_server_time + time_since_last_sync
    
    def get_synced_time(self):
        """获取同步后的时间(秒)"""
        return self.get_synced_timestamp() / 1000
    
    def get_time_offset(self):
        """获取当前时间偏移量"""
        return self.time_offset
    
    def get_sync_status(self):
        """获取同步状态"""
        return {
            'is_synced': self.is_synced,
            'time_offset': self.time_offset,
            'last_server_time': self.last_server_time,
            'last_local_time': self.last_local_time,
            'current_synced_time': self.get_synced_timestamp()
        }

    def force_sync(self):
        return self.sync_time(force=True)
    
    def _format_time(self, timestamp_ms):
        """格式化时间戳为可读时间"""
        try:
            return datetime.fromtimestamp(timestamp_ms / 1000).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        except:
            return "Invalid time"

# 创建全局时间同步管理器实例
time_sync_manager = TimeSyncManager()

# 启动时间同步
time_sync_manager.start()
