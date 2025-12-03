import time
import logging
from binance.client import Client
from binance.enums import SIDE_BUY, SIDE_SELL, FUTURE_ORDER_TYPE_MARKET

# 导入时间同步管理器
try:
    from time_sync_config import time_sync_manager
except ImportError:
    print("警告: 无法导入time_sync_config，请确保该模块存在")
    # 创建一个简单的替代时间同步管理器类
    class DummyTimeSyncManager:
        def __init__(self):
            self.time_offset = 0
        def get_synced_timestamp(self):
            return int(time.time() * 1000)
        def sync_time(self):
            pass
    time_sync_manager = DummyTimeSyncManager()

# 从配置文件导入API密钥
try:
    from config import API_KEY, API_SECRET
except ImportError:
    print("警告: 无法导入配置文件，请确保config.py存在且包含API_KEY和API_SECRET")
    # 如果无法导入配置文件，则使用空字符串
    API_KEY = ''
    API_SECRET = ''

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('BinanceTakeProfitMonitor')

class TimeSyncedBinanceClient(Client):
    """支持时间同步的Binance客户端"""
    def _get_timestamp(self):
        # 使用时间同步管理器提供的同步后时间戳
        return time_sync_manager.get_synced_timestamp()

class TakeProfitMonitor:
    """币安U本位合约主动止盈监控器"""
    def __init__(self, api_key, api_secret, check_interval=60, profit_threshold=1.3):
        """
        初始化止盈监控器
        :param api_key: Binance API Key
        :param api_secret: Binance API Secret
        :param check_interval: 检查间隔（秒）
        :param profit_threshold: 止盈阈值倍数（默认1.35倍）
        """
        # 使用支持时间同步的客户端
        self.client = TimeSyncedBinanceClient(api_key, api_secret)
        self.check_interval = check_interval
        self.profit_threshold = profit_threshold
        
        # 用于跟踪已执行止盈的币种，防止重复执行
        self.take_profit_executed = set()
        
        # 运行状态
        self.running = False
    
    def safe_request(self, request_func, *args, **kwargs):
        """带重试机制的请求包装函数"""
        max_retries = 3
        retry_delay = 1
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                timestamp = time_sync_manager.get_synced_timestamp()
                current_local_time = int(time.time() * 1000)
                if abs(timestamp - current_local_time) > 10000:
                    logger.warning(f"时间戳差异过大: {timestamp - current_local_time}ms，强制重新同步")
                    if hasattr(time_sync_manager, 'sync_time'):
                        time_sync_manager.sync_time()
                    timestamp = time_sync_manager.get_synced_timestamp()
                if 'timestamp' not in kwargs:
                    kwargs['timestamp'] = timestamp
                logger.debug(f"使用时间戳: {timestamp} (偏移: {time_sync_manager.time_offset}ms)")
                
                return request_func(*args, **kwargs)
            except Exception as e:
                # 处理时间同步错误(1021错误码)
                if hasattr(e, 'code') and e.code == 1021:
                    error_msg = str(e)
                    logger.warning(f"时间戳错误 (尝试 {attempt + 1}/{max_retries}): {error_msg}")
                    if "ahead" in error_msg.lower():
                        logger.warning("时间戳超前服务器时间，但将继续尝试")
                    elif "behind" in error_msg.lower():
                        logger.warning("时间戳落后服务器时间，重新同步")
                        if hasattr(time_sync_manager, 'sync_time'):
                            time_sync_manager.sync_time()
                    time.sleep(retry_delay)
                    continue
                if "Timestamp" in str(e) or "time" in str(e).lower():
                    logger.warning(f"时间戳相关错误 (尝试 {attempt + 1}/{max_retries})，重新同步")
                    if hasattr(time_sync_manager, 'sync_time'):
                        time_sync_manager.sync_time()
                    time.sleep(retry_delay)
                    continue
                
                last_exception = e
                logger.warning(f"请求失败 (尝试 {attempt + 1}/{max_retries}): {str(e)}")
                time.sleep(retry_delay)
        
        logger.error(f"请求失败，已达到最大重试次数: {str(last_exception)}")
        raise last_exception
    
    def get_positions(self):
        """使用同步后的时间戳获取持仓信息"""
        try:
            # 注释掉不需要显示的日志
            # logger.info(f"使用同步时间戳 {time_sync_manager.get_synced_timestamp()} 获取持仓信息")
            positions = self.safe_request(self.client.futures_position_information)
            # 只返回有持仓的交易对
            return [pos for pos in positions if float(pos['positionAmt']) != 0]
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []
    
    def get_current_price(self, symbol):
        """获取当前价格"""
        try:
            ticker = self.safe_request(self.client.futures_symbol_ticker, symbol=symbol)
            return float(ticker['price'])
        except Exception as e:
            logger.error(f"获取{symbol}当前价格失败: {e}")
            return None
    
    def take_profit_half_position(self, symbol, position):
        """市价平仓一半仓位"""
        try:
            position_amt = float(position['positionAmt'])
            entry_price = float(position['entryPrice'])
            current_price = self.get_current_price(symbol)
            
            if current_price is None:
                logger.error(f"无法获取{symbol}当前价格，取消止盈操作")
                return False
            
            # 计算一半仓位
            half_quantity = abs(position_amt) / 2  # 对于空单，position_amt是负数，abs取绝对值
            
            # 获取交易对的 LOT_SIZE 规则，确保数量符合规则
            exchange_info = self.safe_request(self.client.futures_exchange_info)
            symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
            
            if not symbol_info:
                logger.error(f"{symbol} 交易对信息获取失败")
                return False
            
            # 调整数量到符合交易规则
            lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
            if lot_size_filter:
                step_size = float(lot_size_filter['stepSize'])
                min_qty = float(lot_size_filter['minQty'])
                
                # 计算符合 stepSize 的数量
                half_quantity = round(half_quantity / step_size) * step_size
                
                # 确保不小于 minQty
                if half_quantity < min_qty:
                    logger.error(f"{symbol} 计算数量 {half_quantity} 小于最小交易量 {min_qty}")
                    return False
            
            # 确定交易方向 (平多还是平空)
            side = SIDE_SELL if position_amt > 0 else SIDE_BUY  # 多单平仓用SELL，空单平仓用BUY
            
            # 下单平仓一半仓位
            order = self.safe_request(
                self.client.futures_create_order,
                symbol=symbol,
                side=side,
                type=FUTURE_ORDER_TYPE_MARKET,
                quantity=half_quantity,
                reduceOnly=True  # 确保是减仓操作
            )
            
            # 计算盈利百分比 - 考虑多单和空单的不同计算方式
            if position_amt > 0:  # 多单
                profit_percent = ((current_price - entry_price) / entry_price) * 100
            else:  # 空单
                profit_percent = ((entry_price - current_price) / entry_price) * 100
            
            logger.info(f"止盈成功: {symbol} 平掉一半仓位 ({half_quantity}), "
                        f"开仓价: {entry_price}, 当前价: {current_price}, "
                        f"盈利: {profit_percent:.2f}%, 订单ID: {order['orderId']}")
            
            # 记录该币种已执行止盈
            self.take_profit_executed.add(symbol)
            
            return True
        except Exception as e:
            logger.error(f"{symbol} 止盈操作失败: {e}")
            # 如果出现异常，不记录为已执行止盈，以便下次重试
            return False
    
    def check_and_execute_take_profit(self):
        """检查所有持仓是否达到止盈条件并执行止盈操作"""
        try:
            # 获取所有持仓
            positions = self.get_positions()
            
            if not positions:
                logger.info("当前没有持仓")
                return
            
            logger.info(f"当前共有 {len(positions)} 个持仓")
            
            # 获取当前所有有持仓的币种列表
            current_position_symbols = {pos['symbol'] for pos in positions}
            
            # 清理已完全平仓的币种，从take_profit_executed集合中移除
            for symbol in list(self.take_profit_executed):
                if symbol not in current_position_symbols:
                    logger.info(f"{symbol} 已完全平仓，从已执行止盈列表中移除")
                    self.take_profit_executed.remove(symbol)
            
            for position in positions:
                symbol = position['symbol']
                position_amt = float(position['positionAmt'])
                entry_price = float(position['entryPrice'])
                
                # 跳过已执行过止盈的币种
                if symbol in self.take_profit_executed:
                    logger.info(f"{symbol} 已执行过止盈，跳过检查")
                    continue
                
                # 获取当前价格
                current_price = self.get_current_price(symbol)
                if current_price is None:
                    continue
                
                # 计算当前价格相对于开仓价的倍数
                price_ratio = current_price / entry_price if position_amt > 0 else entry_price / current_price
                
                logger.info(f"{symbol}: 开仓价={entry_price}, 当前价={current_price}, 价格倍数={price_ratio:.4f}")
                
                # 检查是否达到止盈条件
                if price_ratio >= self.profit_threshold:
                    logger.info(f"{symbol} 达到止盈条件 (倍数: {price_ratio:.4f} ≥ {self.profit_threshold})")
                    self.take_profit_half_position(symbol, position)
        except Exception as e:
            logger.error(f"检查和执行止盈时发生错误: {e}")
    
    def start(self):
        """启动监控器"""
        logger.info(f"启动主动止盈监控器")
        logger.info(f"检查间隔: {self.check_interval}秒, 止盈阈值: {self.profit_threshold}倍")
        
        self.running = True
        
        try:
            while self.running:
                self.check_and_execute_take_profit()
                
                # 等待下一次检查
                for _ in range(self.check_interval):
                    if not self.running:
                        break
                    time.sleep(1)
        except KeyboardInterrupt:
            logger.info("监控器被用户中断")
        except Exception as e:
            logger.error(f"监控器运行出错: {e}")
        finally:
            self.stop()
    
    def stop(self):
        """停止监控器"""
        self.running = False
        logger.info("币安U本位合约主动止盈监控器已停止")

# 主程序入口
if __name__ == "__main__":
    # 只使用配置文件中的API密钥
    if not API_KEY or not API_SECRET:
        print("错误: 配置文件中缺少API_KEY或API_SECRET，请检查config.py文件")
        exit(1)
    
    # 注释掉不需要显示的信息
    # print("正在使用配置文件中的API密钥...")
    
    # 创建并启动监控器
    monitor = TakeProfitMonitor(
        api_key=API_KEY,
        api_secret=API_SECRET,
        check_interval=300,  # 每分钟检查一次
        profit_threshold=1.3  # 1.3倍止盈
    )
    
    try:
        monitor.start()
    except Exception as e:
        print(f"程序运行出错: {e}")
