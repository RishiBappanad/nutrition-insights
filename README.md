# Nutrition Insights Analytics Engine

A comprehensive Personal Performance & Nutrition Analytics Engine that integrates:
- **Cronometer**: Daily nutritional tracking (macros, micronutrients)
- **Strava**: Cardiovascular activities (runs, rides, swims)
- **Hevy**: Weightlifting sessions and exercise metrics

Correlate nutrition, cardio performance, and lifting progress to generate actionable health insights.

## Features

✅ **Normalized SQLite Database** - Linked schema for multi-source data
✅ **Cronometer Automation** - Playwright-based headless browser automation for CSV extraction
✅ **Strava API Integration** - OAuth2 authentication and activity sync
✅ **Hevy API Integration** - Weightlifting data retrieval
✅ **Type-Safe Data Processing** - Pydantic models with validation
✅ **Error Handling** - Graceful handling of API timeouts and failures
✅ **Modular Architecture** - Clean separation of concerns

## Project Structure

```
nutrition-insights/
├── src/
│   ├── database/
│   │   └── schema.py           # SQLite schema and upsert logic
│   ├── integrations/
│   │   ├── cronometer.py       # Playwright automation for Cronometer
│   │   ├── strava.py           # Strava REST API client
│   │   └── hevy.py             # Hevy API client
│   ├── data_processing/
│   │   └── transform.py        # Data transformation with Pydantic
│   ├── config.py               # Configuration management
│   └── logging_config.py       # Logging setup
├── tests/                       # Unit tests
├── raw_data/                   # Downloaded CSV/JSON storage
├── main.py                     # Orchestration pipeline
├── requirements.txt            # Python dependencies
└── .env.example               # Environment variable template
```

## Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/nutrition-insights.git
   cd nutrition-insights
   ```

2. **Create a Python virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

## Configuration

### Environment Variables

Create a `.env` file with the following:

```env
# Cronometer (for Playwright automation)
CRONOMETER_EMAIL=your_email@example.com
CRONOMETER_PASSWORD=your_password

# Strava OAuth2
STRAVA_CLIENT_ID=your_client_id
STRAVA_CLIENT_SECRET=your_client_secret
STRAVA_REFRESH_TOKEN=your_refresh_token

# Hevy API
HEVY_API_KEY=your_api_key

# Database
DATABASE_PATH=./nutrition_insights.db

# Logging
LOG_LEVEL=INFO
```

## Usage

### Running the Pipeline

```bash
python main.py
```

This will:
1. Initialize the SQLite database
2. Launch Cronometer export via Playwright
3. Sync Strava activities
4. Transform and validate all data
5. Upsert into the database

### Using Individual Components

**Cronometer Export:**
```python
import asyncio
from src.integrations.cronometer import CronometerAutomation

async def export_cronometer():
    async with CronometerAutomation(headless=False) as automation:
        results = await automation.download_all_csvs()
        print(results)

asyncio.run(export_cronometer())
```

**Transform Nutrition Data:**
```python
from src.data_processing.transform import CronometerTransformer

transformer = CronometerTransformer()
records = transformer.transform("raw_data/cronometer_daily_summary_20240115.csv")
for record in records:
    print(record.date, record.protein_g)
```

**Strava API:**
```python
import asyncio
from src.integrations.strava import StravaClient

async def fetch_activities():
    client = StravaClient()
    activities = await client.get_activities()
    print(f"Retrieved {len(activities)} activities")

asyncio.run(fetch_activities())
```

## Database Schema

### Tables

- **daily_nutrition**: Macros and micronutrients from Cronometer
- **cardio_activities**: Runs, rides, swims from Strava
- **weightlifting_sessions**: Workout sessions from Hevy
- **weightlifting_exercises**: Exercises within sessions
- **exercise_sets**: Individual sets with reps, weight, RPE
- **daily_stats**: Aggregated daily metrics for quick queries

### Example Query

```sql
-- Find 12-mile runs on days with >200g protein and heavy leg day
SELECT
    dn.date,
    dn.protein_g,
    ca.activity_type,
    ca.distance_m,
    ca.avg_speed_ms,
    we.exercise_name,
    we.muscle_group
FROM daily_nutrition dn
LEFT JOIN cardio_activities ca ON DATE(ca.activity_date) = dn.date
LEFT JOIN weightlifting_sessions ws ON DATE(ws.session_date) = dn.date
LEFT JOIN weightlifting_exercises we ON ws.id = we.session_id
WHERE dn.protein_g > 200
    AND ca.distance_m >= 19312  -- ~12 miles in meters
    AND we.muscle_group = 'Legs'
ORDER BY dn.date DESC;
```

## API Documentation

### CronometerAutomation
- `login()` - Authenticate with Cronometer
- `navigate_to_export()` - Navigate to export section
- `download_csv()` - Download specific CSV type
- `download_all_csvs()` - Complete workflow

### StravaClient
- `refresh_access_token()` - Refresh OAuth token
- `get_activities()` - Fetch athlete activities

### HevyClient
- `get_workouts()` - Fetch workouts by date range
- `get_workout_details()` - Get detailed workout info

## Testing

```bash
pytest tests/
```

## Error Handling

The application gracefully handles:
- ❌ Login failures with retries
- ❌ API timeouts and rate limits
- ❌ Missing/malformed data files
- ❌ Database connection errors
- ❌ CSV parsing errors

All errors are logged and tracked via the logging system.

## Performance Considerations

- **Crawler Rate Limiting**: 2-second delays between Cronometer downloads
- **Database Indexes**: Optimized queries with indexes on date columns
- **Batch Upserts**: Efficient INSERT OR REPLACE operations
- **Connection Pooling**: Reusable database connections

## Future Enhancements

- [ ] Web dashboard (Streamlit/FastAPI)
- [ ] Scheduled sync with Airflow/APScheduler
- [ ] Machine learning insights (correlation analysis)
- [ ] Health metrics aggregation (sleep, recovery)
- [ ] Export reports (PDF, CSV)
- [ ] Mobile app integration

## License

MIT

## Author

Your Name - [GitHub](https://github.com/yourusername)
