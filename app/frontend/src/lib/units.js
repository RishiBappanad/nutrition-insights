// Unit conversion helpers for metric/imperial display — all backend
// storage stays in metric/mL (height_cm, weight_kg, amount_ml) per the
// existing schema; conversion is purely a display-layer concern here,
// matching the pattern already established for water_target_ml
// (nutrition-diary-design.md: "store mL internally, display-unit is a UI
// preference only").

const KG_PER_LB = 0.45359237
const CM_PER_IN = 2.54
const ML_PER_FL_OZ = 29.5735
const ML_PER_CUP = 236.588

export function kgToLb(kg) {
  return kg / KG_PER_LB
}

export function lbToKg(lb) {
  return lb * KG_PER_LB
}

export function cmToIn(cm) {
  return cm / CM_PER_IN
}

export function inToCm(inches) {
  return inches * CM_PER_IN
}

/** cm -> {feet, inches} for a feet+inches height display. */
export function cmToFeetInches(cm) {
  const totalInches = cmToIn(cm)
  const feet = Math.floor(totalInches / 12)
  const inches = Math.round(totalInches % 12)
  return { feet, inches }
}

export function feetInchesToCm(feet, inches) {
  return inToCm(Number(feet) * 12 + Number(inches))
}

export function mlToFlOz(ml) {
  return ml / ML_PER_FL_OZ
}

export function flOzToMl(flOz) {
  return flOz * ML_PER_FL_OZ
}

export function mlToCups(ml) {
  return ml / ML_PER_CUP
}

export function cupsToMl(cups) {
  return cups * ML_PER_CUP
}
