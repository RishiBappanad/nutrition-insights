import pandas as pd


def calculate_bmr(file_path="tdee_tracking_log.csv", window=21, alpha=0.1):
    """Calculate BMR. Uses linear model if last N days are continuous,
    otherwise falls back to exponential smoothing for gapped data.

    Args:
        file_path: Path to TDEE tracking CSV
        window: Number of days to consider
        alpha: Smoothing factor for exponential model (used only when gaps exist)
    """
    from pathlib import Path
    if not Path(file_path).exists():
        return "No tracking data found. Run a sync first."

    df = pd.read_csv(file_path, parse_dates=["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    # Exclude today's row for calorie data (day isn't over), but keep today's weight
    today = pd.Timestamp.now().normalize()
    today_weight = df.loc[df["Date"] == today, "Weight_lbs"].dropna()
    df = df[df["Date"] < today]

    # Add today's weight back as a weight-only row
    if not today_weight.empty:
        today_row = pd.DataFrame({"Date": [today], "Weight_lbs": [today_weight.iloc[0]],
                                  "Calories_Consumed": [None], "Active_Calories_Burned": [None]})
        df = pd.concat([df, today_row], ignore_index=True)

    complete = df.dropna(subset=["Calories_Consumed", "Active_Calories_Burned", "Weight_lbs"])
    if len(complete) < 7:
        return "Need at least 7 days with complete data."

    # Check if the last `window` complete days form a continuous streak
    recent = complete.tail(window)
    dates = recent["Date"].diff().dt.days.iloc[1:]
    is_continuous = (dates == 1).all() and len(recent) >= 7

    if is_continuous:
        return _linear_bmr(recent)
    else:
        return _exponential_bmr(df, window, alpha)


def _linear_bmr(streak):
    """Linear model using regression over continuous days.
    Weight on day N reflects day N-1's intake (morning weigh-in)."""
    import numpy as np

    net_intake = (streak["Calories_Consumed"] - streak["Active_Calories_Burned"]).iloc[:-1].mean()

    weights = streak["Weight_lbs"].iloc[1:].values
    day_nums = np.arange(len(weights), dtype=float)
    slope, _ = np.polyfit(day_nums, weights, 1)

    bmr = net_intake - (slope * 3500)
    return round(bmr, 0)


def _exponential_bmr(df, window, alpha):
    """Model for gapped data. Uses median of first/last third of weigh-ins,
    with net intake (consumed - burned) averaged over the same span."""
    has_cals = df.dropna(subset=["Calories_Consumed", "Active_Calories_Burned"])
    if len(has_cals) < 7:
        return "Need at least 7 days with complete data."

    recent_cals = has_cals.tail(window)
    date_start = recent_cals["Date"].iloc[0]

    # Weight window: from first calorie day to today (includes today's weigh-in)
    weigh_ins = df[df["Date"] >= date_start].dropna(subset=["Weight_lbs"])

    if len(weigh_ins) < 6:
        return "Need at least 6 weigh-ins in the window."

    n_days = (weigh_ins["Date"].iloc[-1] - weigh_ins["Date"].iloc[0]).days
    if n_days < 7:
        return "Need at least 7 days between weigh-ins."

    # Median of first/last third for stability
    n_third = max(3, len(weigh_ins) // 3)
    weight_start = weigh_ins["Weight_lbs"].iloc[:n_third].median()
    weight_end = weigh_ins["Weight_lbs"].iloc[-n_third:].median()
    weight_change = weight_end - weight_start

    # Net intake: only days with complete data, up to (not including) last weigh-in day
    last_weight_date = weigh_ins["Date"].iloc[-1]
    cal_span = has_cals[(has_cals["Date"] >= date_start) & (has_cals["Date"] < last_weight_date)]
    if len(cal_span) < 5:
        cal_span = recent_cals

    net_intake = (cal_span["Calories_Consumed"] - cal_span["Active_Calories_Burned"]).mean()

    bmr = net_intake - (weight_change * 3500 / n_days)
    return round(bmr, 0)


if __name__ == "__main__":
    print(f"Estimated BMR: {calculate_bmr()} kcal")
