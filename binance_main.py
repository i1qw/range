import time
import pandas as pd
from datetime import datetime
from binance.client import Client
from binance.enums import *
# 导入时间同步管理器
from time_sync_config import time_sync_manager
# 导入配置参数
from config import LONG_LEVERAGE, LONG_AMOUNT


# 创建一个支持时间同步的自定义Binance客户端类
class TimeSyncedBinanceClient(Client):
    def __init__(self, api_key, api_secret, **kwargs):
        # 先调用父类的初始化
        super().__init__(api_key, api_secret, **kwargs)
        # 确保时间同步管理器已同步时间
        if hasattr(time_sync_manager, 'sync_time'):
            time_sync_manager.sync_time()
    
    def _get_timestamp(self):
        # 使用时间同步管理器提供的同步后时间戳
        return time_sync_manager.get_synced_timestamp()
    
    # 添加期货市场24小时行情数据的方法
    def futures_ticker_24hr(self, **params):
        """获取期货24小时行情数据"""
        return self._request_futures_api('get', 'ticker/24hr', data=params)
    
    def _request_futures_api(self, method, path, signed=False, version=1, **kwargs):
        """发送期货API请求的辅助方法"""
        # 确保时间戳是最新的
        if 'data' not in kwargs:
            kwargs['data'] = {}
        
        if 'timestamp' not in kwargs['data']:
            kwargs['data']['timestamp'] = self._get_timestamp()
        
        # 调用父类的_request_futures_api方法
        return super()._request_futures_api(method, path, signed=signed, version=version, **kwargs)

class BinanceFuturesTrader:
    def __init__(self, api_key, api_secret):
        """
        初始化交易器
        :param api_key: Binance API
        :param api_secret: Binance API KEY
        """
        # 使用自定义的支持时间同步的客户端
        self.client = TimeSyncedBinanceClient(api_key, api_secret)
        # 从配置文件导入杠杆参数
        self.long_leverage = LONG_LEVERAGE  # 多单杠杆
        # 从配置文件导入交易金额设置
        self.long_amount = LONG_AMOUNT  # 多单保证金金额(USDT)
        # 网络请求参数
        self.request_timeout = 10  # 请求超时时间(秒)
        self.max_retries = 3  # 最大重试次数
        self.retry_delay = 1  # 重试延迟(秒)

        # 新增：用于跟踪止损订单关系
        self.order_relations = {}  # 格式: {symbol: {'stop_loss': orderId}}

        # 初始化交易对列表
        self.symbols = self.get_qualified_symbols()
        self.setup_account()
        print(f"初始化完成，将监控{len(self.symbols)}个交易对")

    def validate_symbol(self, symbol):
        """验证交易对是否有效"""
        try:
            info = self.safe_request(self.client.futures_exchange_info)
            return any(s['symbol'] == symbol and s['status'] == 'TRADING'
                       for s in info['symbols'])
        except:
            return False

    def refresh_symbol_list(self):
        """强制刷新交易对列表"""
        print("强制刷新交易对列表...")
        self.symbols = self.get_qualified_symbols()
        print(f"最新有效交易对: {self.symbols}")

    def safe_request(self, request_func, *args, **kwargs):
        """带重试机制的请求包装函数，包含时间同步错误处理"""
        last_exception = None
        for attempt in range(self.max_retries):
            try:
                return request_func(*args, **kwargs)
            except Exception as e:
                # 特定错误码直接抛出，不重试
                if hasattr(e, 'code') and e.code in [-4059]:  # 不需要更改持仓模式的错误
                    raise e
                
                # 处理时间同步错误(1021错误码)
                if hasattr(e, 'code') and e.code == 1021:
                    print(f"检测到时间同步错误(1021)，重新同步时间...")
                    time_sync_manager.sync_time()
                    print(f"重新同步后时间偏移量: {time_sync_manager.time_offset}ms")
                    time.sleep(self.retry_delay)
                    continue

                last_exception = e
                print(f"请求失败 (尝试 {attempt + 1}/{self.max_retries}): {str(e)}")
                time.sleep(self.retry_delay)
        raise last_exception if last_exception else Exception("未知请求错误")

    def get_qualified_symbols(self):
        """获取24小时涨幅≥10%且成交量＞1亿美元的交易对"""
        try:
            print("正在获取24小时行情数据...")
            tickers = self.safe_request(self.client.futures_ticker_24hr)
            print(f"成功获取{len(tickers)}个交易对的24小时行情数据")
            qualified_symbols = []
            
            for ticker in tickers:
                symbol = ticker['symbol']
                
                if not symbol.endswith('USDT'):
                    continue
                
                try:
                    price_change_percent = float(ticker.get('priceChangePercent', 0))
                    quote_volume = float(ticker.get('quoteVolume', 0))
                except (ValueError, TypeError):
                    continue
                
                if price_change_percent >= 10.0 and quote_volume > 100000000:
                    if self.validate_symbol(symbol):
                        qualified_symbols.append({
                            'symbol': symbol,
                            'price_change_percent': price_change_percent,
                            'quote_volume': quote_volume
                        })
            
            print(f"找到{len(qualified_symbols)}个符合条件的交易对（涨幅≥10%且成交量＞1亿美元）")
            
            if qualified_symbols:
                qualified_symbols.sort(key=lambda x: x['price_change_percent'], reverse=True)
                all_symbols = [item['symbol'] for item in qualified_symbols]
                
                print(f"全部符合条件的交易对 ({len(qualified_symbols)}个):")
                for i, item in enumerate(qualified_symbols, 1):
                    change_str = f"{item['price_change_percent']:>6.2f}%"
                    volume_str = f"${item['quote_volume']/1000000:>8.1f}M"
                    print(f"  {i:2d}. {item['symbol']:15} | 涨幅: {change_str} | 成交量: {volume_str}")
                
                print(f"\n将监控全部{len(qualified_symbols)}个符合条件的交易对")
                
                return all_symbols
            else:
                print("未找到符合条件的交易对，返回空列表")
                return []
            
        except Exception as e:
            print(f"获取符合条件的交易对失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def update_symbols(self):
        """无条件更新为最新的符合条件的标的"""
        print("更新符合条件的标的...")
        try:
            new_symbols = self.get_qualified_symbols()
            if new_symbols:  # 只有获取成功时才更新
                self.symbols = new_symbols
                print(f"最新监控列表: {self.symbols}")
            else:
                print("保持原有交易对列表")
        except Exception as e:
            print(f"更新交易对列表失败: {e}")

    def setup_account(self):
        """设置账户参数"""
        try:
            # 先获取当前持仓模式
            position_mode = self.safe_request(
                self.client.futures_get_position_mode
            )

            # 如果已经是单向持仓模式，则不需要再次设置
            if not position_mode['dualSidePosition']:
                print("账户已是单向持仓模式，无需更改")
                return

            # 尝试设置单向持仓模式
            try:
                self.safe_request(
                    self.client.futures_change_position_mode,
                    dualSidePosition=False
                )
                print("成功设置为单向持仓模式")
            except Exception as e:
                if hasattr(e, 'code') and e.code == -4059:
                    print("账户已是单向持仓模式")
                else:
                    print(f"账户设置警告: {e}")
        except Exception as e:
            print(f"获取持仓模式失败: {e}")

    def get_account_balance(self):
        """获取U本位合约账户USDT余额"""
        try:
            balance = self.safe_request(self.client.futures_account_balance)
            for asset in balance:
                if asset['asset'] == 'USDT':
                    return float(asset['balance'])
            return 0.0
        except Exception as e:
            print(f"获取余额失败: {e}")
            return 0.0

    def get_positions(self):
        """获取U本位合约持仓数据（只获取多单）"""
        try:
            positions = self.safe_request(self.client.futures_position_information)
            return [pos for pos in positions if float(pos['positionAmt']) > 0]
        except Exception as e:
            print(f"获取持仓失败: {e}")
            return []

    def _get_raw_klines(self, symbol, interval='1h', limit=100):
        """获取原始K线数据的私有方法，供其他方法调用"""
        try:
            klines = self.safe_request(
                self.client.futures_klines,
                symbol=symbol,
                interval=interval,
                limit=limit
            )
            if not klines:
                print(f"获取{symbol}原始K线数据为空")
                return None
            return klines
        except Exception as e:
            print(f"获取{symbol}原始K线数据失败: {e}")
            return None

    def get_klines_data(self, symbol, interval='1h', limit=100):
        """处理K线数据并计算指标"""
        try:
            # 复用_get_raw_klines方法获取数据
            klines = self._get_raw_klines(symbol, interval, limit)
            if not klines:
                return None

            df = pd.DataFrame(klines, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base', 'taker_buy_quote', 'ignore'
            ])

            numeric_cols = ['open', 'high', 'low', 'close']
            df[numeric_cols] = df[numeric_cols].apply(pd.to_numeric, axis=1)

            # 检查数据量是否足够计算移动平均线
            if len(df) >= 60:
                # 计算移动平均线，使用bfill()处理可能的NaN值
                df['ma_20'] = df['close'].rolling(20).mean().bfill()
                df['ma_60'] = df['close'].rolling(60).mean().bfill()
            elif len(df) >= 20:
                # 至少有20根K线，计算MA20，MA60设为NaN
                df['ma_20'] = df['close'].rolling(20).mean().bfill()
                df['ma_60'] = pd.NA
                print(f"{symbol} K线数据量({len(df)})不足60根，无法计算MA60")
            else:
                # 数据量不足20根K线
                df['ma_20'] = pd.NA
                df['ma_60'] = pd.NA
                print(f"{symbol} K线数据量({len(df)})不足20根，无法计算移动平均线")
                return None

            # 确保最后一行数据没有NaN值
            if df.iloc[-1][['ma_20', 'ma_60']].isna().any():
                print(f"{symbol} 最新K线数据移动平均线计算结果包含NaN值")
                return None

            return df.iloc[-1]
        except Exception as e:
            print(f"处理{symbol}K线数据失败: {e}")
            return None

    def get_current_hour_klines(self, symbol):
        """获取当前小时的K线数据（用于动态止损判断）"""
        try:
            # 复用_get_raw_klines方法获取数据
            klines = self._get_raw_klines(symbol, '1h', 1)
            if not klines:
                return None

            kline = {
                'open': float(klines[0][1]),
                'high': float(klines[0][2]),
                'low': float(klines[0][3]),
                'close': float(klines[0][4]),
                'price_change_pct': (float(klines[0][4]) - float(klines[0][1])) / float(klines[0][1]) * 100
            }
            return kline
        except Exception as e:
            print(f"获取{symbol}当前小时K线失败: {e}")
            return None

    def adjust_leverage(self, symbol):
        """调整杠杆"""
        leverage = self.long_leverage
        try:
            self.safe_request(
                self.client.futures_change_leverage,
                symbol=symbol,
                leverage=leverage
            )
            return True
        except Exception as e:
            print(f"调整{symbol}杠杆失败: {e}")
            return False

    def calculate_quantity(self, symbol, usdt_amount, leverage):
        """计算合约数量，确保符合交易对的 LOT_SIZE 规则"""
        try:
            # 获取当前价格
            ticker = self.safe_request(self.client.futures_symbol_ticker, symbol=symbol)
            price = float(ticker['price'])
            raw_quantity = usdt_amount * leverage / price

            # 获取交易对的 LOT_SIZE 规则
            exchange_info = self.safe_request(self.client.futures_exchange_info)
            symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)

            if not symbol_info:
                print(f"{symbol} 交易对信息获取失败")
                return None

            lot_size_filter = next((f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'), None)
            if not lot_size_filter:
                print(f"{symbol} 没有 LOT_SIZE 限制")
                return None

            step_size = float(lot_size_filter['stepSize'])
            min_qty = float(lot_size_filter['minQty'])

            # 计算符合 stepSize 的数量
            quantity = round(raw_quantity / step_size) * step_size

            # 确保不小于 minQty
            if quantity < min_qty:
                print(f"{symbol} 计算数量 {quantity} 小于最小交易量 {min_qty}")
                return None

            # 格式化数量，避免科学计数法或多余小数
            try:
                quantity_str = f"{quantity:.{symbol_info['quantityPrecision']}f}"
                quantity = float(quantity_str.rstrip('0').rstrip('.') if '.' in quantity_str else quantity_str)
            except:
                # 如果无法获取quantityPrecision，使用备用方法
                quantity = float(
                    f"{quantity:.8f}".rstrip('0').rstrip('.') if '.' in f"{quantity:.8f}" else f"{quantity:.0f}")

            # 修改：简化日志输出
            print(f"{symbol} 计算数量: {quantity} (价格: {price})")
            return quantity

        except Exception as e:
            print(f"计算 {symbol} 数量失败: {e}")
            return None

    def place_order(self, symbol, side, quantity):
        """
        下单函数
        :param symbol: 交易对
        :param side: 买卖方向 (BUY/SELL)
        :param quantity: 数量
        """
        if not self.adjust_leverage(symbol):
            return None

        try:
            order = self.safe_request(
                self.client.futures_create_order,
                symbol=symbol,
                side=side,
                type=FUTURE_ORDER_TYPE_MARKET,
                quantity=quantity
            )
            # 修改：简化日志输出
            direction = "买入" if side == SIDE_BUY else "卖出"
            print(f"下单成功: 订单ID {order['orderId']} | 数量: {quantity} | 方向: {direction}")
            return order
        except Exception as e:
            print(f"下单失败: {e}")
            return None

    def set_stop_loss(self, symbol, entry_price, kline=None):
        """设置移动止损单"""
        position = self.get_position(symbol)
        if not position or float(position['positionAmt']) == 0:
            return None

        # 获取交易对的 pricePrecision
        exchange_info = self.safe_request(self.client.futures_exchange_info)
        symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
        if not symbol_info:
            print(f"{symbol} 交易对信息获取失败")
            return None

        price_precision = symbol_info['pricePrecision']

        # 初始止损设置：开仓时K线最低价下方0.1%的位置
        stop_price = round(kline['low'] * 0.999, price_precision)

        # 修改：删除原始止损设置日志，合并到下面的日志中

        # 先撤销原有止损单
        self.cancel_associated_orders(symbol)

        # 创建新止损单
        try:
            order = self.safe_request(
                self.client.futures_create_order,
                symbol=symbol,
                side=SIDE_SELL,
                type=FUTURE_ORDER_TYPE_STOP_MARKET,
                stopPrice=stop_price,
                closePosition=True
            )
            # 修改：简化日志输出
            print(f"止损设置成功: 订单ID {order['orderId']} | 止损价: {stop_price} | 类型: 卖出止损")
            if symbol not in self.order_relations:
                self.order_relations[symbol] = {}
            self.order_relations[symbol]['stop_loss'] = order['orderId']
            return order
        except Exception as e:
            print(f"止损单设置失败: {e}")
            return None

    def update_stop_loss(self, symbol, entry_price):
        """更新移动止损（每小时59分检查）"""
        try:
            position = self.get_position(symbol)
            if not position or float(position['positionAmt']) == 0:
                print(f"{symbol} 无持仓，跳过止损更新")
                return None

            # 获取当前小时K线
            hour_kline = self.get_current_hour_klines(symbol)
            if not hour_kline:
                print(f"{symbol} 获取K线失败，跳过止损更新")
                return None

            # 检查涨跌幅是否满足条件
            price_change = hour_kline['price_change_pct']
            if price_change >= 1.0:
                print(f"{symbol} 满足止损调整条件，当前涨跌幅: {price_change:.2f}%")

                # 获取交易对精度信息
                exchange_info = self.safe_request(self.client.futures_exchange_info)
                symbol_info = next((s for s in exchange_info['symbols'] if s['symbol'] == symbol), None)
                if not symbol_info:
                    print(f"{symbol} 交易对信息获取失败")
                    return None

                price_precision = symbol_info['pricePrecision']

                # 多单止损价更新至最新小时K线最低价下方0.1%的位置
                new_stop_price = round(hour_kline['low'] * 0.999, price_precision)
                print(f"{symbol} 多单止损价更新至: {new_stop_price}")

                # 先撤销原有止损单
                self.cancel_associated_orders(symbol)

                # 创建新止损单
                try:
                    print(f"准备提交止损单参数: "
                          f"symbol={symbol}, "
                          f"side=SELL, "
                          f"type=STOP_MARKET, "
                          f"stopPrice={new_stop_price}, "
                          f"closePosition=True")

                    order = self.safe_request(
                        self.client.futures_create_order,
                        symbol=symbol,
                        side=SIDE_SELL,
                        type='STOP_MARKET',
                        stopPrice=new_stop_price,
                        closePosition=True
                    )
                    print(f"验证订单是否真正创建")
                    order_id = order['orderId']
                    verified_order = self.safe_request(
                        self.client.futures_get_order,
                        symbol=symbol,
                        orderId=order_id
                    )
                    if verified_order['status'] == 'NEW':
                        print(f"止损单验证成功: {order_id}")
                        if symbol not in self.order_relations:
                            self.order_relations[symbol] = {}
                        self.order_relations[symbol]['stop_loss'] = order_id
                        return order
                    else:
                        print(f"止损单状态异常: {verified_order['status']}")
                        return None

                except Exception as e:
                    print(f"止损单创建失败: {str(e)}")
                    if hasattr(e, 'code'):
                        print(f"错误代码: {e.code}")
                    if hasattr(e, 'message'):
                        print(f"错误信息: {e.message}")
                    return None
        except Exception as e:
            print(f"更新止损时发生未预期错误: {str(e)}")
            return None

    def get_position(self, symbol):
        """获取指定交易对的持仓（带验证）"""
        try:
            positions = self.safe_request(self.client.futures_position_information)
            for pos in positions:
                if pos['symbol'] == symbol and float(pos['positionAmt']) > 0:
                    # 验证仓位数据完整性
                    if all(k in pos for k in ['positionAmt', 'entryPrice', 'markPrice']):
                        return pos
            return None
        except Exception as e:
            print(f"获取{symbol}持仓失败: {str(e)}")
            return None

    def check_open_long_signal(self, kline):
        """检查开多信号"""
        if kline is None:
            return False

        # 计算当前K线的涨跌幅（基于开盘价和收盘价）
        price_change = (kline['close'] - kline['open']) / kline['open'] * 100
        
        # 计算开盘价与最低价之间的幅度
        open_low_change = (kline['open'] - kline['low']) / kline['open'] * 100
        
        # 计算收盘价在max(ma20, ma60)之上的幅度
        max_ma = max(kline['ma_20'], kline['ma_60'])
        above_ma_change = (kline['close'] - max_ma) / max_ma * 100

        # 开多信号条件：
        # 1. 1%≤当前小时涨幅＜4%
        # 2. 当前小时收盘价＞MA60 且 当前小时收盘价＞MA20
        # 3. 当前小时开盘价与最低价之间的幅度小于4%
        # 4. 当前小时收盘价在max（ma20，ma60）之上且幅度小于11%
        return (
            1.0 <= price_change < 4.0 and
            kline['close'] > kline['ma_60'] and kline['close'] > kline['ma_20'] and
            abs(open_low_change) < 4.0 and
            0 < above_ma_change < 11.0
        )

    def cancel_associated_orders(self, symbol):
        """撤销与指定交易对关联的所有止损单"""
        try:
            # 获取所有当前委托
            open_orders = self.safe_request(self.client.futures_get_open_orders, symbol=symbol)

            for order in open_orders:
                # 检查是否是止损单
                if order['type'].upper() in ['STOP_MARKET'] or order['reduceOnly']:
                    try:
                        # 撤销订单
                        self.safe_request(
                            self.client.futures_cancel_order,
                            symbol=symbol,
                            orderId=order['orderId']
                        )
                        print(f"已撤销订单: {order['orderId']} (类型: {order['type']})")
                    except Exception as e:
                        print(f"撤销订单{order['orderId']}失败: {e}")

            # 清除该交易对的订单关系记录
            if symbol in self.order_relations:
                del self.order_relations[symbol]

        except Exception as e:
            print(f"获取{symbol}委托单失败: {e}")

    def check_order_execution(self):
        """检查订单执行情况"""
        try:
            positions = self.get_positions()
            position_symbols = [pos['symbol'] for pos in positions]

            # 检查所有有订单关系的交易对
            for symbol in list(self.order_relations.keys()):
                # 如果该交易对已经没有持仓，说明订单已执行
                if symbol not in position_symbols:
                    print(f"{symbol} 的止损单已成交，仓位已平")
                    self.cancel_associated_orders(symbol)

        except Exception as e:
            print(f"检查订单执行情况失败: {e}")

    def handle_existing_position(self, symbol):
        """
        处理现有持仓，确保符合开仓条件
        :param symbol: 交易对
        :return: 是否可以进行开仓 (True/False)
        """
        position = self.get_position(symbol)
        if not position or float(position['positionAmt']) == 0:
            return True  # 无持仓，可以开仓

        # 检查是否已有同向持仓
        if float(position['positionAmt']) > 0:
            print(f"{symbol} 已有同向持仓，禁止重复开仓")
            return False

        return False  # 其他情况不允许开仓

    def format_position_log(self, positions):
        """简化版持仓信息格式化"""
        if not positions:
            return "当前无持仓"
        
        log_lines = ["当前持仓:"]
        for i, pos in enumerate(positions, 1):
            symbol = pos['symbol']
            amount = float(pos['positionAmt'])
            entry_price = float(pos['entryPrice'])
            unrealized_pnl = float(pos['unRealizedProfit'])
            
            pnl_sign = "+" if unrealized_pnl >= 0 else ""
            
            log_lines.append(
                f"{i}. {symbol:10} | 多头 | "
                f"入场: {entry_price:8.4f} | 盈亏: {pnl_sign}{unrealized_pnl:7.2f} USDT"
            )
        
        return "\n".join(log_lines)

    def run_strategy(self):
        """运行交易策略"""
        print("自动交易系统启动...")
        print(f"账户余额: {self.get_account_balance()} USDT")

        while True:
            now = datetime.now()

            try:
                # 每小时第57分钟检查订单执行情况
                if now.minute == 57 and now.second == 0:
                    print(f"\n57分检查订单执行情况: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                    self.check_order_execution()
                    time.sleep(1)  # 避免1秒内重复执行
                    continue

                # 每小时第58分钟更新交易对列表
                if now.minute == 58 and now.second == 0:
                    try:
                        self.update_symbols()
                    except Exception as e:
                        print(f"更新交易对列表失败: {e}")
                    time.sleep(1)
                    continue

                # 每小时第59分钟执行交易策略和移动止损检查
                if now.minute == 59 and now.second == 0:
                    print(f"\n执行策略检查: {now.strftime('%Y-%m-%d %H:%M:%S')}")
                    try:
                        current_positions = self.get_positions()
                        # 使用格式化输出持仓信息
                        print(self.format_position_log(current_positions))

                        # 检查所有持仓，如果仓位为0但仍有订单，则撤销
                        for symbol in set(pos['symbol'] for pos in current_positions):
                            position = self.get_position(symbol)
                            if position and float(position['positionAmt']) == 0:
                                self.cancel_associated_orders(symbol)
                                print(f"{symbol} 仓位已平，已撤销关联订单")

                        # 检查所有持仓是否需要更新止损
                        for pos in current_positions:
                            symbol = pos['symbol']
                            entry_price = float(pos['entryPrice'])
                            self.update_stop_loss(symbol, entry_price)

                    except Exception as e:
                        print(f"获取持仓失败: {e}")
                        current_positions = []

                    # 合并监控列表：符合条件的标的 + 当前持仓标的（去重）
                    symbols_to_check = list(set(self.symbols + [pos['symbol'] for pos in current_positions]))

                    for symbol in symbols_to_check:
                        try:
                            print(f"\n分析交易对: {symbol}")
                            kline = self.get_klines_data(symbol)
                            if kline is None:
                                continue

                            # 检查开仓信号（仅对符合条件的标的执行）
                            if symbol in self.symbols:
                                if self.check_open_long_signal(kline):
                                    print(f"{symbol} 触发开多信号")
                                    if self.handle_existing_position(symbol):
                                        quantity = self.calculate_quantity(symbol, self.long_amount, self.long_leverage)
                                        if quantity:
                                            order = self.place_order(symbol, SIDE_BUY, quantity)
                                            if order:
                                                # 获取开仓价格
                                                ticker = self.safe_request(self.client.futures_symbol_ticker,
                                                                           symbol=symbol)
                                                entry_price = float(ticker['price'])
                                                # 设置止损
                                                self.set_stop_loss(symbol, entry_price, kline)

                        except Exception as e:
                            print(f"处理{symbol}时出错: {e}")

                    time.sleep(1)
                else:
                    time.sleep(1)
            except Exception as e:
                print(f"主循环发生错误: {e}")
                time.sleep(10)  # 发生错误时等待10秒再继续


if __name__ == "__main__":
    from config import API_KEY,API_SECRET

    trader = BinanceFuturesTrader(API_KEY, API_SECRET)
    trader.run_strategy()
