import pandas as pd


def calculate_bmr(file_path="tdee_tracking_log.csv", min_days=7):
    df = pd.read_csv(file_path, parse_dates=["Date"])
    df = df.dropna(subset=["Calories_Consumed", "Active_Calories_Burned", "Weight_lbs"])
    df = df.sort_values("Date").reset_index(drop=True)

    if len(df) < min_days:
        return f"Need at least {min_days} days of complete data."

    # Find the longest trailing streak of consecutive days
    streak_end = len(df) - 1
    streak_start = streak_end
    while streak_start > 0:
        diff = (df.loc[streak_start, "Date"] - df.loc[streak_start - 1, "Date"]).days
        if diff != 1:
            break
        streak_start -= 1

    streak = df.iloc[streak_start : streak_end + 1]

    if len(streak) < min_days:
        return f"Need at least {min_days} continuous days. Longest trailing streak: {len(streak)} days."

    avg_intake = streak["Calories_Consumed"].mean()
    avg_burn = streak["Active_Calories_Burned"].mean()
    weight_start = streak["Weight_lbs"].iloc[0]
    weight_end = streak["Weight_lbs"].iloc[-1]
    weight_change = weight_end - weight_start
    n_days = len(streak)

    # TDEE = avg intake - (weight change in lbs * 3500 cal/lb / days)
    tdee = avg_intake - (weight_change * 3500 / n_days)

    # BMR = TDEE - active calories (Cronometer already includes TEF + baseline activity)
    bmr = tdee - avg_burn

    return round(bmr, 0)


if __name__ == "__main__":
    print(f"Estimated BMR: {calculate_bmr()} kcal")
