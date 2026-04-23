# Database Schema Documentation

## Overview

The SQLite database uses a normalized schema to efficiently store and query correlated data from three sources: Cronometer (nutrition), Strava (cardio), and Hevy (lifting).

## Tables

### `daily_nutrition`
Stores daily macronutrient and micronutrient data from Cronometer.

**Columns:**
- `id` (INTEGER PRIMARY KEY): Unique record identifier
- `date` (TEXT UNIQUE): Date in YYYY-MM-DD format
- `calories` (REAL): Total daily calories
- `protein_g` (REAL): Protein in grams
- `carbs_g` (REAL): Carbohydrates in grams
- `fat_g` (REAL): Fat in grams
- `fiber_g` (REAL): Dietary fiber in grams
- `sugar_g` (REAL): Sugar in grams
- `sodium_mg` (REAL): Sodium in milligrams
- `potassium_mg` (REAL): Potassium in milligrams
- `calcium_mg` (REAL): Calcium in milligrams
- `iron_mg` (REAL): Iron in milligrams
- `vitamin_d_iu` (REAL): Vitamin D in IU
- `magnesium_mg` (REAL): Magnesium in milligrams
- `raw_csv_path` (TEXT): Path to source CSV file
- `imported_at` (TIMESTAMP): Import timestamp
- `updated_at` (TIMESTAMP): Last update timestamp

**Indexes:**
- `idx_daily_nutrition_date` on `date` for fast date-based queries

---

### `cardio_activities`
Stores individual cardio activities from Strava (runs, rides, swims, etc.)

**Columns:**
- `id` (INTEGER PRIMARY KEY): Unique record identifier
- `strava_activity_id` (INTEGER UNIQUE): Strava's activity ID
- `activity_date` (DATE): Date of the activity
- `activity_type` (TEXT): Type of activity (Run, Ride, Swim, Walk, etc.)
- `name` (TEXT): Activity name/title
- `distance_m` (REAL): Distance in meters
- `duration_seconds` (INTEGER): Duration in seconds
- `avg_speed_ms` (REAL): Average speed in m/s
- `max_speed_ms` (REAL): Maximum speed in m/s
- `elevation_gain_m` (REAL): Elevation gain in meters
- `avg_heartrate` (INTEGER): Average heart rate in bpm
- `max_heartrate` (INTEGER): Maximum heart rate in bpm
- `total_elevation_loss_m` (REAL): Total elevation loss in meters
- `calories_burned` (REAL): Estimated calories burned
- `strava_json_data` (TEXT): Raw JSON response from Strava API
- `imported_at` (TIMESTAMP): Import timestamp
- `updated_at` (TIMESTAMP): Last update timestamp

**Indexes:**
- `idx_cardio_activities_date` on `activity_date` for fast date-based queries

---

### `weightlifting_sessions`
Stores weightlifting sessions from Hevy.

**Columns:**
- `id` (INTEGER PRIMARY KEY): Unique record identifier
- `hevy_session_id` (TEXT UNIQUE): Hevy's session ID
- `session_date` (DATE): Date of the session
- `start_time` (TIMESTAMP): Session start time
- `end_time` (TIMESTAMP): Session end time
- `duration_minutes` (INTEGER): Session duration in minutes
- `notes` (TEXT): User notes about the session
- `hevy_json_data` (TEXT): Raw JSON from Hevy API
- `imported_at` (TIMESTAMP): Import timestamp
- `updated_at` (TIMESTAMP): Last update timestamp

**Indexes:**
- `idx_weightlifting_sessions_date` on `session_date` for fast date-based queries

---

### `weightlifting_exercises`
Stores individual exercises within a weightlifting session (one-to-many relationship).

**Columns:**
- `id` (INTEGER PRIMARY KEY): Unique record identifier
- `session_id` (INTEGER FOREIGN KEY): Reference to `weightlifting_sessions.id`
- `hevy_exercise_id` (TEXT): Hevy's exercise ID
- `exercise_name` (TEXT): Exercise name (Back Squat, Bench Press, etc.)
- `muscle_group` (TEXT): Primary muscle group (Legs, Chest, Back, etc.)
- `is_superset` (BOOLEAN): Whether part of a superset
- `hevy_json_data` (TEXT): Raw JSON from Hevy API
- `imported_at` (TIMESTAMP): Import timestamp

**Foreign Keys:**
- `session_id` → `weightlifting_sessions.id`

---

### `exercise_sets`
Stores individual sets within an exercise (one-to-many relationship).

**Columns:**
- `id` (INTEGER PRIMARY KEY): Unique record identifier
- `exercise_id` (INTEGER FOREIGN KEY): Reference to `weightlifting_exercises.id`
- `set_number` (INTEGER): Set number within exercise
- `is_warmup` (BOOLEAN): Whether this is a warmup set
- `reps` (INTEGER): Number of repetitions
- `weight_kg` (REAL): Weight in kilograms
- `rpe` (REAL): Rate of Perceived Exertion (1-10)
- `notes` (TEXT): Set-specific notes
- `hevy_json_data` (TEXT): Raw JSON from Hevy API
- `imported_at` (TIMESTAMP): Import timestamp

**Foreign Keys:**
- `exercise_id` → `weightlifting_exercises.id`

---

### `daily_stats`
Aggregated daily summary table for fast querying across all domains.

**Columns:**
- `id` (INTEGER PRIMARY KEY): Unique record identifier
- `date` (DATE UNIQUE): Date in YYYY-MM-DD
- `nutrition_id` (INTEGER FOREIGN KEY): Reference to `daily_nutrition.id`
- `avg_cardio_pace` (REAL): Average pace of all cardio activities that day (km/h)
- `total_cardio_calories` (REAL): Sum of cardio calories burned
- `total_cardio_distance_m` (REAL): Total distance of all cardio
- `cardio_activity_count` (INTEGER): Number of cardio activities
- `max_cardio_heartrate` (INTEGER): Highest heart rate from cardio
- `lifting_session_count` (INTEGER): Number of lifting sessions
- `lifting_duration_minutes` (INTEGER): Total lifting duration
- `total_sets_completed` (INTEGER): Total sets across all lifting sessions
- `created_at` (TIMESTAMP): Creation timestamp
- `updated_at` (TIMESTAMP): Last update timestamp

**Foreign Keys:**
- `nutrition_id` → `daily_nutrition.id`

**Indexes:**
- `idx_daily_stats_date` on `date` for fast date-based queries

---

## Relationships Diagram

```
daily_nutrition (1) ──→ (many) daily_stats
                            ↓
                       (multiple date-based joins)
                            ↓
cardio_activities ────────────┘
                    
weightlifting_sessions (1) ──→ (many) weightlifting_exercises (1) ──→ (many) exercise_sets
```

---

## Example Queries

### 1. Find activities with high nutrition intake
```sql
SELECT
    dn.date,
    dn.calories,
    dn.protein_g,
    ca.activity_type,
    ca.distance_m,
    ca.avg_speed_ms
FROM daily_nutrition dn
LEFT JOIN cardio_activities ca ON DATE(ca.activity_date) = dn.date
WHERE dn.protein_g > 200 AND dn.calories > 2500
ORDER BY dn.date DESC;
```

### 2. Correlate heavy lifts with recovery metrics
```sql
SELECT
    dn.date,
    dn.calories,
    SUM(es.weight_kg * es.reps) as total_volume,
    COUNT(DISTINCT ws.id) as sessions,
    ca.avg_heartrate
FROM daily_nutrition dn
LEFT JOIN weightlifting_sessions ws ON DATE(ws.session_date) = dn.date
LEFT JOIN weightlifting_exercises we ON ws.id = we.session_id
LEFT JOIN exercise_sets es ON we.id = es.exercise_id
LEFT JOIN cardio_activities ca ON DATE(ca.activity_date) = dn.date
GROUP BY dn.date
ORDER BY dn.date DESC;
```

### 3. Analyze protein intake on heavy leg days
```sql
SELECT
    dn.date,
    dn.protein_g,
    COUNT(DISTINCT CASE WHEN we.muscle_group = 'Legs' THEN we.id END) as leg_exercises,
    SUM(CASE WHEN we.muscle_group = 'Legs' THEN es.weight_kg * es.reps ELSE 0 END) as leg_volume
FROM daily_nutrition dn
LEFT JOIN weightlifting_sessions ws ON DATE(ws.session_date) = dn.date
LEFT JOIN weightlifting_exercises we ON ws.id = we.session_id
LEFT JOIN exercise_sets es ON we.id = es.exercise_id
WHERE dn.protein_g IS NOT NULL
GROUP BY dn.date
HAVING leg_volume > 0
ORDER BY dn.date DESC;
```

---

## Data Types

- **INTEGER**: Whole numbers (IDs, counts, durations)
- **REAL**: Decimal numbers (calories, macros, speed, weight)
- **TEXT**: Strings (names, types, notes, JSON)
- **DATE**: Date-only values (YYYY-MM-DD)
- **TIMESTAMP**: DateTime values with time component
- **BOOLEAN**: 0 (false) or 1 (true)

---

## Upsert Strategy

All tables use `INSERT OR REPLACE` (UPSERT) to handle both initial inserts and updates:

```sql
INSERT INTO table_name (...columns...)
VALUES (...values...)
ON CONFLICT(unique_column) DO UPDATE SET
    col1 = excluded.col1,
    col2 = excluded.col2,
    updated_at = CURRENT_TIMESTAMP
```

This approach:
- ✅ Automatically creates records that don't exist
- ✅ Updates existing records with fresh data
- ✅ Tracks changes via `updated_at` timestamp
- ✅ Prevents duplicate key errors
