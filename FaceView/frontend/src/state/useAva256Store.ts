import { create } from "zustand";
import { immer } from "zustand/middleware/immer";

import { api } from "@/lib/api";
import type {
  Ava256DatasetResponse,
  Ava256FrameRow,
  Ava256NeutralResponse,
  Ava256RangesByLabel,
  LandmarkFrame
} from "@/lib/types";
import { useAnnotationStore } from "@/state/useAnnotationStore";

// Ava-256 slice, separate from useAnnotationStore so the Nersemble/FaceScape
// code paths stay untouched. One capture = one keyline video + per-frame CSV
// (backend services/ava256_labels.py).
interface Ava256State {
  dataset?: Ava256DatasetResponse;
  isLoading: boolean;
  error?: string;
  selectedCapture?: string;
  frames: Ava256FrameRow[];
  isDone: boolean;
  isFramesLoading: boolean;
  isSaving: boolean;
  isFinishing: boolean;
  fetchDataset: () => Promise<void>;
  selectCapture: (captureId: string) => Promise<void>;
  applyRanges: (ranges: Ava256RangesByLabel) => Promise<boolean>;
  finish: () => Promise<boolean>;

  // Neutral frame · ear tops tab (backend services/ava256_neutral.py). One
  // click per camera per side; a side is saved once it has >= 2 cameras.
  neutral?: Ava256NeutralResponse;
  isNeutralLoading: boolean;
  neutralError?: string;
  selectedCameras: string[];
  landmarkCache: Record<string, LandmarkFrame>; // "<capture>:<camera>"
  earAddMode: EarSide | null;
  earClicksLeft: Record<string, { x: number; y: number }>;
  earClicksRight: Record<string, { x: number; y: number }>;
  isSavingEar: boolean;
  loadNeutral: (captureId: string) => Promise<void>;
  toggleCamera: (cameraId: string) => void;
  setSelectedCameras: (cameraIds: string[]) => void;
  loadNeutralLandmarks: (cameraId: string, force?: boolean) => Promise<void>;
  startEarAddMode: (ear: EarSide) => void;
  cancelEarAddMode: () => void;
  recordEarClick: (cameraId: string, x: number, y: number) => void;
  clearEarClicks: () => void;
  saveEarPoints: () => Promise<void>;
}

type EarSide = "left" | "right";

function toast(tone: "success" | "error", message: string) {
  useAnnotationStore.setState((state) => {
    state.toast = { id: Date.now(), tone, message };
  });
}

// fetchJSON errors look like 'Request failed (409): {"detail":"..."}'.
function errorText(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const match = message.match(/^Request failed \((\d+)\): (.*)$/s);
  if (!match) return message;
  try {
    const detail = JSON.parse(match[2]).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  } catch {
    return match[2] || message;
  }
}

export const useAva256Store = create<Ava256State>()(
  immer((set, get) => ({
    dataset: undefined,
    isLoading: false,
    error: undefined,
    selectedCapture: undefined,
    frames: [],
    isDone: false,
    isFramesLoading: false,
    isSaving: false,
    isFinishing: false,
    neutral: undefined,
    isNeutralLoading: false,
    neutralError: undefined,
    selectedCameras: [],
    landmarkCache: {},
    earAddMode: null,
    earClicksLeft: {},
    earClicksRight: {},
    isSavingEar: false,

    async fetchDataset() {
      set((state) => {
        state.isLoading = true;
        state.error = undefined;
      });
      try {
        const dataset = await api.getAva256Dataset();
        set((state) => {
          state.dataset = dataset;
        });
      } catch (error) {
        set((state) => {
          state.error = errorText(error);
        });
      } finally {
        set((state) => {
          state.isLoading = false;
        });
      }
    },

    async selectCapture(captureId: string) {
      set((state) => {
        state.selectedCapture = captureId;
        state.frames = [];
        state.isDone = false;
        state.isFramesLoading = true;
      });
      void get().loadNeutral(captureId);
      try {
        const response = await api.getAva256Frames(captureId);
        if (get().selectedCapture !== captureId) return;
        set((state) => {
          state.frames = response.frames;
          state.isDone = response.is_done;
        });
      } catch (error) {
        toast("error", `Failed to load frames for ${captureId}: ${errorText(error)}`);
      } finally {
        set((state) => {
          state.isFramesLoading = false;
        });
      }
    },

    async applyRanges(ranges: Ava256RangesByLabel) {
      const captureId = get().selectedCapture;
      if (!captureId) return false;
      set((state) => {
        state.isSaving = true;
      });
      try {
        const response = await api.labelAva256Frames(captureId, ranges);
        set((state) => {
          if (state.selectedCapture === captureId) state.frames = response.frames;
          const capture = state.dataset?.captures.find((c) => c.capture_id === captureId);
          if (capture) {
            capture.counts = { Valid: 0, Invalid: 0, Rerun: 0, unconfirmed: 0 };
            for (const frame of response.frames) capture.counts[frame.status] += 1;
          }
        });
        toast(
          "success",
          `Labels saved for ${captureId}` + (response.warnings.length ? ` (${response.warnings.join("; ")})` : "")
        );
        return true;
      } catch (error) {
        toast("error", `Failed to save labels: ${errorText(error)}`);
        return false;
      } finally {
        set((state) => {
          state.isSaving = false;
        });
      }
    },

    async finish() {
      const captureId = get().selectedCapture;
      if (!captureId) return false;
      set((state) => {
        state.isFinishing = true;
      });
      try {
        const response = await api.finishAva256Capture(captureId);
        set((state) => {
          if (state.selectedCapture === captureId) state.isDone = true;
          const capture = state.dataset?.captures.find((c) => c.capture_id === captureId);
          if (capture) {
            capture.is_done = true;
            capture.label_status = "confirmed";
          }
        });
        toast(
          "success",
          `${captureId} done: ${response.counts.Valid ?? 0} valid frame(s)` +
            (response.export_path ? ` exported to ${response.export_path}` : " (nothing exported)")
        );
        return true;
      } catch (error) {
        toast("error", `Done failed: ${errorText(error)}`);
        return false;
      } finally {
        set((state) => {
          state.isFinishing = false;
        });
      }
    },

    async loadNeutral(captureId: string) {
      set((state) => {
        state.neutral = undefined;
        state.neutralError = undefined;
        state.isNeutralLoading = true;
        state.selectedCameras = [];
        state.earAddMode = null;
        state.earClicksLeft = {};
        state.earClicksRight = {};
      });
      try {
        const neutral = await api.getAva256Neutral(captureId);
        if (get().selectedCapture !== captureId) return;
        set((state) => {
          state.neutral = neutral;
          state.selectedCameras = neutral.default_cameras;
        });
      } catch (error) {
        if (get().selectedCapture !== captureId) return;
        set((state) => {
          state.neutralError = errorText(error);
        });
      } finally {
        set((state) => {
          state.isNeutralLoading = false;
        });
      }
    },

    toggleCamera(cameraId: string) {
      set((state) => {
        const order = state.neutral?.cameras.map((c) => c.camera_id) ?? [];
        const selected = new Set(state.selectedCameras);
        if (selected.has(cameraId)) selected.delete(cameraId);
        else selected.add(cameraId);
        state.selectedCameras = order.filter((c) => selected.has(c)); // keep left -> right
      });
    },

    setSelectedCameras(cameraIds: string[]) {
      set((state) => {
        state.selectedCameras = cameraIds;
      });
    },

    async loadNeutralLandmarks(cameraId: string, force = false) {
      const captureId = get().selectedCapture;
      if (!captureId) return;
      const key = `${captureId}:${cameraId}`;
      if (!force && get().landmarkCache[key]) return;
      try {
        const frame = await api.getAva256NeutralLandmarks(captureId, cameraId);
        set((state) => {
          state.landmarkCache[key] = frame;
        });
      } catch (error) {
        console.warn(`Ava-256 neutral landmarks failed for ${key}: ${errorText(error)}`);
      }
    },

    startEarAddMode(ear: EarSide) {
      set((state) => {
        state.earAddMode = ear;
      });
    },

    cancelEarAddMode() {
      // Clicks are kept, so switching sides or cancelling never loses work.
      set((state) => {
        state.earAddMode = null;
      });
    },

    recordEarClick(cameraId: string, x: number, y: number) {
      set((state) => {
        if (state.earAddMode === "left") state.earClicksLeft[cameraId] = { x, y };
        else if (state.earAddMode === "right") state.earClicksRight[cameraId] = { x, y };
      });
    },

    clearEarClicks() {
      set((state) => {
        state.earClicksLeft = {};
        state.earClicksRight = {};
      });
    },

    async saveEarPoints() {
      const captureId = get().selectedCapture;
      if (!captureId || get().isSavingEar) return;
      const sides: EarSide[] = (["left", "right"] as EarSide[]).filter(
        (side) => Object.keys(side === "left" ? get().earClicksLeft : get().earClicksRight).length >= 2
      );
      if (!sides.length) return;
      set((state) => {
        state.isSavingEar = true;
      });
      const saved: string[] = [];
      try {
        for (const side of sides) {
          const clicks = side === "left" ? get().earClicksLeft : get().earClicksRight;
          try {
            const response = await api.saveAva256EarPoint(captureId, side, clicks);
            const worst = Math.max(...Object.values(response.reprojection_error_px));
            saved.push(`${side} (${response.cameras_used.length} cameras, max reprojection ${worst.toFixed(1)} px)`);
            set((state) => {
              if (side === "left") state.earClicksLeft = {};
              else state.earClicksRight = {};
              const index = side === "left" ? 2 : 9;
              if (state.neutral && state.selectedCapture === captureId && !state.neutral.ear_tops.includes(index)) {
                state.neutral.ear_tops = [...state.neutral.ear_tops, index].sort((a, b) => a - b);
              }
            });
          } catch (error) {
            toast("error", `Saving the ${side} ear top failed: ${errorText(error)}`);
          }
        }
        if (get().selectedCapture === captureId) {
          await Promise.all(get().selectedCameras.map((cameraId) => get().loadNeutralLandmarks(cameraId, true)));
          // Cached tiles that aren't shown right now go stale -- drop them.
          set((state) => {
            for (const key of Object.keys(state.landmarkCache)) {
              const cameraId = key.slice(captureId.length + 1);
              if (key.startsWith(`${captureId}:`) && !state.selectedCameras.includes(cameraId)) {
                delete state.landmarkCache[key];
              }
            }
          });
        }
        if (saved.length) {
          set((state) => {
            state.earAddMode = null;
          });
          toast("success", `Ear top saved for ${captureId}: ${saved.join("; ")}`);
        }
      } finally {
        set((state) => {
          state.isSavingEar = false;
        });
      }
    }
  }))
);
