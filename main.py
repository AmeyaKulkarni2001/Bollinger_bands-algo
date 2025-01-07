import ccxt
from fastapi import FastAPI
import uvicorn
from dotenv import load_dotenv
import pandas as pd
import time
import logging
import threading
import os

load_dotenv()

logger = logging.getLogger(__name__)
logging.basicConfig(filename='tesing.log', encoding='utf-8', level=logging.INFO)

# Initialize exchange
exchange = ccxt.binance({
    'apiKey': os.getenv('APIKEY'),
    'secret': os.getenv('SECRET'),
    'options': {
        'defaultType': 'spot',  # Ensure it's for spot trading
    },
})
exchange.set_sandbox_mode(True)
app = FastAPI()

bot_state = {
    "profit": 0.0,
    "position": None,
    "entry_price": None,
    "take_profit": None,
    "stop_loss": None,
    "last_close": None,
    "trades": [],
    "running": True,  # Flag to control bot execution
}

# Fetch historical data
def fetch_data(symbol, timeframe='1m', limit=100):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except ccxt.BaseError as e:
        print(f"Error fetching data: {e}")
        exit(1)

# Calculate Indicators
def calculate_indicators(data):
    # Bollinger Bands
    window = 20
    std_dev = 2
    data['sma'] = data['close'].rolling(window=window).mean()
    data['std'] = data['close'].rolling(window=window).std()
    data['upper_band'] = data['sma'] + (std_dev * data['std'])
    data['lower_band'] = data['sma'] - (std_dev * data['std'])

    # RSI
    delta = data['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    data['rsi'] = 100 - (100 / (1 + rs))

    # MACD
    short_ema = data['close'].ewm(span=12, adjust=False).mean()
    long_ema = data['close'].ewm(span=26, adjust=False).mean()
    data['macd'] = short_ema - long_ema
    data['signal_line'] = data['macd'].ewm(span=9, adjust=False).mean()

    # ATR
    data['tr'] = data[['high', 'low', 'close']].apply(
        lambda row: max(row['high'] - row['low'], 
                        abs(row['high'] - row.get('close', row['low'])), 
                        abs(row['low'] - row.get('close', row['high']))),
        axis=1
    )
    data['atr'] = data['tr'].rolling(window=14).mean()

    # ADX
    data['+DM'] = data['high'].diff().where((data['high'].diff() > data['low'].diff()) & (data['high'].diff() > 0), 0)
    data['-DM'] = (-data['low'].diff()).where((data['low'].diff() > data['high'].diff()) & (data['low'].diff() > 0), 0)
    data['+DM_smoothed'] = data['+DM'].rolling(window=14).mean()
    data['-DM_smoothed'] = data['-DM'].rolling(window=14).mean()
    data['tr_smoothed'] = data['tr'].rolling(window=14).mean()
    data['+DI'] = (data['+DM_smoothed'] / data['tr_smoothed']) * 100
    data['-DI'] = (data['-DM_smoothed'] / data['tr_smoothed']) * 100
    data['dx'] = (abs(data['+DI'] - data['-DI']) / (data['+DI'] + data['-DI'])) * 100
    data['adx'] = data['dx'].rolling(window=14).mean()

    return data

# Create a buy order
def place_buy_order(symbol, quantity):
    try:
        order = exchange.create_order(symbol, 'market', 'buy', quantity)
        bot_state['trades'].append(order)
        print(f"Buy order placed: {order}")
    except Exception as e:
        print(f"Error placing buy order: {e}")

# Create a sell order
def place_sell_order(symbol, quantity):
    try:
        order = exchange.create_order(symbol, 'market', 'sell', quantity)
        bot_state['trades'].append(order)
        print(f"Sell order placed: {order}")
    except Exception as e:
        print(f"Error placing sell order: {e}")

# Trading Bot with TP at Bollinger Mean
def scalping_bot():
    symbol = 'BTC/USDT'
    position = None  # Track open position
    entry_price = None
    stop_loss = None
    take_profit = None
    quantity = 0.00026

    while True:
        try:
            # Fetch data
            data = fetch_data(symbol)
            data = calculate_indicators(data)

            # Get the latest indicator values
            last_close = data['close'].iloc[-1]
            lower_band = data['lower_band'].iloc[-1]
            upper_band = data['upper_band'].iloc[-1]
            mean_band = data['sma'].iloc[-1]  # Middle Bollinger Band (mean)
            rsi = data['rsi'].iloc[-1]
            macd = data['macd'].iloc[-1]
            signal_line = data['signal_line'].iloc[-1]
            atr = data['atr'].iloc[-1]
            adx = data['adx'].iloc[-1]

            trailing_stop = None


            # If no position is open, look for entry signals
            if position is None:
                if last_close < lower_band + 50 and rsi < 36 and macd > signal_line:
                    place_buy_order(symbol, quantity)
                    position = "long"
                    entry_price = last_close
                    stop_loss = entry_price - 1 * atr
                    take_profit = entry_price + 3 * atr  # Fixed take-profit for ranging markets
                    if adx > 25:
                        trailing_stop = stop_loss
                    bot_state.update({
                        "position" : "long",
                        "stop_loss" : entry_price - 1 * atr,
                        "take_profit" : entry_price + 3 * atr,
                        "entry_price" : last_close
                    })
                    print(f"*************Buy Signal - Price: {last_close} take profit {take_profit} stop loss {stop_loss}*************")
                    logging.info(f"*************Buy Signal - Price: {last_close} take profit {take_profit} stop loss {stop_loss}*************")


                elif last_close > upper_band - 50 and rsi > 63 and macd < signal_line:
                    place_sell_order(symbol, quantity)
                    position = "short"
                    entry_price = last_close
                    stop_loss = entry_price + 1 * atr
                    take_profit = entry_price - 3 * atr  # Fixed take-profit for ranging markets
                    if adx > 25:
                        trailing_stop = stop_loss
                    bot_state.update({
                        "position" : "short",
                        "stop_loss" : entry_price - 1 * atr,
                        "take_profit" : entry_price + 3 * atr,
                        "entry_price" : last_close
                    })
                    print(f"*************Sell Signal - Price: {last_close} take profit {take_profit} stop loss {stop_loss}*************")
                    logging.info(f"*************Sell Signal - Price: {last_close} take profit {take_profit} stop loss {stop_loss}*************")

                else:
                    print(f"Lower Band: {lower_band} <Current price {last_close}> Upper Band: {upper_band} 36 < {rsi} < 63  macd {macd} signal line {signal_line}")
                    print(f"For Buy: 1) band: {last_close < lower_band + 50} 2) rsi: {rsi < 36} 3) macd > signal: {macd > signal_line}")
                    print(f"For Sell: 1) band: {last_close > upper_band - 50} 2) rsi: {rsi > 63} 3) macd < signal: {macd < signal_line}")


            # If a position is open, check TP/SL conditions
            elif position == "long":
                if adx is not None and adx > 25:  # Trending market logic
                    if trailing_stop is None:  # Initialize trailing_stop if not set
                        trailing_stop = last_close - atr  # Default to an initial value
                    trailing_stop = max(trailing_stop, last_close - atr)
                    stop_loss = max(stop_loss, trailing_stop) 

                if trailing_stop is not None and last_close >= entry_price + 4 * atr:
                    place_sell_order(symbol, quantity)
                    print(f"*************Take Profit Hit - Selling at {last_close}*************")
                    print(f"Profit = {last_close - entry_price}")
                    bot_state['profit'] += last_close - entry_price
                    bot_state.update({
                        "position" : None,
                        "stop_loss" : None,
                        "take_profit" : None,
                        "entry_price" : None
                    })
                    logging.info(f"Profit = {last_close - entry_price}")
                    position = None
                elif last_close <= stop_loss:
                    place_sell_order(symbol, quantity)
                    print(f"-----------Stop Loss Hit - Selling at {last_close}-----------")
                    print(f"Loss = {last_close - entry_price}")
                    bot_state['profit'] += last_close - entry_price
                    bot_state.update({
                        "position" : None,
                        "stop_loss" : None,
                        "take_profit" : None,
                        "entry_price" : None
                    })
                    logging.info(f"Loss = {last_close - entry_price}")
                    position = None
                else:
                    print(f"Long position maintained take profit {take_profit} stop loss {stop_loss} last close {last_close}")

            elif position == "short":
                if adx is not None and adx > 25:  # Trending market logic
                    if trailing_stop is None:  # Initialize trailing_stop if not set
                        trailing_stop = last_close + atr  # Default to an initial value
                    trailing_stop = min(trailing_stop, last_close - atr)
                    stop_loss = min(stop_loss, trailing_stop)

                if trailing_stop is not None and last_close <= entry_price - 4 * atr:
                    place_buy_order(symbol, quantity)
                    print(f"*************Take Profit Hit - Buying at {last_close}*************")
                    print(f"Profit = {entry_price - last_close}")
                    bot_state['profit'] += last_close - entry_price
                    bot_state.update({
                        "position" : None,
                        "stop_loss" : None,
                        "take_profit" : None,
                        "entry_price" : None
                    })
                    logging.info(f"Profit = {entry_price - last_close}")
                    position = None
                elif last_close >= stop_loss:
                    place_buy_order(symbol, quantity)
                    print(f"-----------Stop Loss Hit - Buying at {last_close}-----------")
                    print(f"Loss = {entry_price - last_close}")
                    bot_state['profit'] += last_close - entry_price
                    bot_state.update({
                        "position" : None,
                        "stop_loss" : None,
                        "take_profit" : None,
                        "entry_price" : None
                    })
                    logging.info(f"Loss = {entry_price - last_close}")
                    position = None
                else:
                    print(f"short position maintained take profit {take_profit} stop loss {stop_loss} last close {last_close}")

            time.sleep(30)  # Wait for the next 5-minute candle
        except Exception as e:
            print(f"Error: {e}")
            exit(1)


# API Endpoints
@app.get("/status")
def get_status():
    return bot_state

@app.post("/stop")
def stop_bot():
    bot_state["running"] = False
    return {"message": "Bot stopping..."}

@app.post("/start")
def start_bot():
    if not bot_state["running"]:
        bot_state["running"] = True
        threading.Thread(target=scalping_bot, daemon=True).start()
    return {"message": "Bot started."}

# Run the bot
if __name__ == "__main__":
    threading.Thread(target=scalping_bot, daemon=True).start()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

