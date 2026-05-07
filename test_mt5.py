import MetaTrader5 as mt5

mt5.initialize()
account = mt5.account_info()
print(account)
mt5.shutdown()
