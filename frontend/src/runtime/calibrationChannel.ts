/** Server-bound calibration commands. HTTP accepted is not an applied session. */

type Sender = (
  type: string,
  payload: Record<string, unknown>,
) => Promise<Record<string, unknown>>;

let sender: Sender | null = null;

export function bindCalibrationChannel(next: Sender): void {
  sender = next;
}

export function clearCalibrationChannel(next?: Sender): void {
  if (!next || sender === next) sender = null;
}

export function calibrationChannelBound(): boolean {
  return sender != null;
}

export async function sendCalibrationCommand(
  type: string,
  payload: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (!sender) {
    throw new Error("Калібрування доступне лише з відкритої сесії керування");
  }
  const result = await sender(type, payload);
  const status = String(result.status ?? "");
  if (status === "failed" || result.ok === false) {
    throw new Error(String(result.error ?? result.error_message ?? "Калібрування відхилено"));
  }
  if (status && status !== "applied") {
    throw new Error("Команду прийнято, але прилад її ще не застосував");
  }
  return result;
}
