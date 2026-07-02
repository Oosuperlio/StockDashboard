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

# Copy data files (CSV results for Sector Monitor & daily signals)
COPY backtest_sector_subsector_results.csv ./
COPY backtest_sector_improvement.csv ./
COPY backtest_4way_results.csv ./
COPY data/signals/ ./data/signals/

# Set port
ENV PORT=8000

# Run Streamlit
CMD ["streamlit", "run", "app.py", "--server.port=8000", "--server.address=0.0.0.0"]
