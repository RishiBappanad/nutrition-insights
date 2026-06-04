import pandas as pd


def calculate_bmr(file_path="tdee_tracking_log.csv", window=14, alpha=0.1):
    """Calculate BMR. Uses linear model if last N days are continuous,
    otherwise falls back to exponential smoothing for gapped data.

    Args:
        file_path: Path to TDEE tracking CSV
        window: Number of days to consider
        alpha: Smoothing factor for exponential model (used only when gaps exist)
    """
    df = pd.read_csv(file_path, parse_dates=["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    # Exclude today's row — the day isn't over so data is incomplete
    today = pd.Timestamp.now().normalize()
    df = df[df["Date"] < today]

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
    """Linear model using rolling average over continuous days.
    Weight on day N reflects day N-1's intake (morning weigh-in)."""
    avg_intake = streak["Calories_Consumed"].iloc[:-1].mean()
    avg_burn = streak["Active_Calories_Burned"].iloc[:-1].mean()
    weight_start = streak["Weight_lbs"].iloc[1]
    weight_end = streak["Weight_lbs"].iloc[-1]
    weight_change = weight_end - weight_start
    n_days = len(streak) - 1

    tdee = avg_intake - (weight_change * 3500 / n_days)
    bmr = tdee - avg_burn
    return round(bmr, 0)


def _exponential_bmr(df, window, alpha):
    """Model for gapped data. Uses raw weigh-ins with exponential smoothing
    only to average multiple weigh-ins, and actual calendar span for rate."""
    # Need rows with intake + burn for averaging
    has_cals = df.dropna(subset=["Calories_Consumed", "Active_Calories_Burned"])
    if len(has_cals) < 7:
        return "Need at least 7 days with complete data."

    recent_cals = has_cals.tail(window)
    avg_intake = recent_cals["Calories_Consumed"].mean()
    avg_burn = recent_cals["Active_Calories_Burned"].mean()

    # For weight, use actual weigh-ins within the calorie window
    date_start = recent_cals["Date"].iloc[0]
    date_end = recent_cals["Date"].iloc[-1]
    weigh_ins = df[(df["Date"] >= date_start) & (df["Date"] <= date_end)].dropna(subset=["Weight_lbs"])

    if len(weigh_ins) < 2:
        return "Need at least 2 weigh-ins in the window."

    # Average first 3 and last 3 weigh-ins to smooth noise
    n_avg = min(3, len(weigh_ins) // 2)
    weight_start = weigh_ins["Weight_lbs"].iloc[:n_avg].mean()
    weight_end = weigh_ins["Weight_lbs"].iloc[-n_avg:].mean()
    weight_change = weight_end - weight_start
    n_days = (weigh_ins["Date"].iloc[-1] - weigh_ins["Date"].iloc[0]).days

    if n_days < 7:
        return "Need at least 7 days between weigh-ins."

    tdee = avg_intake - (weight_change * 3500 / n_days)
    bmr = tdee - avg_burn
    return round(bmr, 0)


if __name__ == "__main__":
    print(f"Estimated BMR: {calculate_bmr()} kcal")
