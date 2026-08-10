FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app and database modules
COPY app.py .
COPY database/ ./database/
COPY pattern_detector.py .
COPY pattern_annotator.py .
COPY predictor.py .
COPY indicator_calculator.py .
COPY indicator_signals.py .
COPY signal_scanner.py .
COPY optimize_exits_by_sector.py .
COPY dynamic_stops_tab.py .

# Copy data files (CSV results + dynamic stops config)
COPY backtest_sector_subsector_results.csv ./
COPY backtest_sector_improvement.csv ./
COPY backtest_4way_results.csv ./
COPY data/signals/ ./data/signals/
COPY data/dynamic_stops/stop_config.json ./data/dynamic_stops/
# Price database (DuckDB) — 信號卡「📍 現價」從此讀取；缺少會全部顯示「—」
COPY data/prices.ddb ./data/

# Set port
ENV PORT=8000

# Run Streamlit
CMD ["streamlit", "run", "app.py", "--server.port=8000", "--server.address=0.0.0.0"]
