# Crypto Arbitrage Scanner Bot

A real-time cryptocurrency arbitrage scanner that monitors price differences across three Iranian exchanges (Nobitex, Bitpin, and Wallex) and sends instant notifications via Telegram when profitable opportunities are detected.

## Features

- 🔍 **Real-time Arbitrage Detection**: Scans 36+ cryptocurrencies across 3 exchanges simultaneously
- 📱 **Telegram Integration**: Users can subscribe/unsubscribe to receive alerts
- 💰 **Profit Calculation**: Accounts for exchange fees and calculates net profit
- 🚀 **Fast & Efficient**: Uses async/await for concurrent API calls
- 📊 **User Management**: Anyone can start/stop receiving alerts
- 💾 **Persistent Storage**: Subscribers are saved to file and restored on restart
- 🎯 **Multiple Trading Pairs**: Supports major coins, altcoins, and memecoins

## Supported Exchanges

| Exchange | Taker Fee | API |
|----------|-----------|-----|
| Nobitex  | 0.13%     | REST |
| Bitpin   | 0.10%     | REST |
| Wallex   | 0.15%     | REST |

## Supported Cryptocurrencies

The bot monitors 36+ cryptocurrencies including:
- **Major Coins**: BTC, ETH, SOL, BNB, XRP, ADA, AVAX, DOT, LINK, NEAR, SUI, APT
- **Altcoins**: TRX, LTC, BCH, ATOM, FTM, INJ, RENDER, FET, TIA, ARB, OP
- **Memecoins**: DOGE, SHIB, PEPE, FLOKI, BONK, WIF, BOME
- **TON Ecosystem**: TON, NOT, DOGS, HMSTR, CATI

## Prerequisites

- Python 3.8+
- A Telegram Bot Token (create one via [@BotFather](https://t.me/botfather))
- Internet connection
