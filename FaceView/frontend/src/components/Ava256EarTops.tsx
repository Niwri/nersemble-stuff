import { useEffect, useMemo } from "react";

import { api } from "@/lib/api";
import type { Ava256NeutralCamera } from "@/lib/types";
import { useAva256Store } from "@/state/useAva256Store";
import { ImageTile } from "./ImageTile";

// Same extras as FaceScape's grid (see LandmarkOverlay's extraVisibleIndices).
const EXTRA_VISIBLE_INDICES = [69, 72];
const SIDES: Ava256NeutralCamera["side"][] = ["left", "front", "right", "unknown"];
const SIDE_LABEL: Record<Ava256NeutralCamera["side"], string> = {
  left: "Left",
  front: "Front",
  right: "Right",
  unknown: "Cameras"
};

// "Add Left/Right Ear Top" for Ava-256, on the capture's neutral frame (the
// same frame wrap_script_ava256's neutral_frame.py picks). Mirrors
// FaceScapePlayer's ear controls: click the ear top in >= 2 cameras, Save
// triangulates it into landmarks_ava-256/<capture>/ear_tops.json.
export function Ava256EarTops() {
  const {
    selectedCapture,
    neutral,
    isNeutralLoading,
    neutralError,
    selectedCameras,
    landmarkCache,
    earAddMode,
    earClicksLeft,
    earClicksRight,
    isSavingEar,
    loadNeutral,
    toggleCamera,
    setSelectedCameras,
    loadNeutralLandmarks,
    startEarAddMode,
    cancelEarAddMode,
    recordEarClick,
    clearEarClicks,
    saveEarPoints
  } = useAva256Store((state) => ({
    selectedCapture: state.selectedCapture,
    neutral: state.neutral,
    isNeutralLoading: state.isNeutralLoading,
    neutralError: state.neutralError,
    selectedCameras: state.selectedCameras,
    landmarkCache: state.landmarkCache,
    earAddMode: state.earAddMode,
    earClicksLeft: state.earClicksLeft,
    earClicksRight: state.earClicksRight,
    isSavingEar: state.isSavingEar,
    loadNeutral: state.loadNeutral,
    toggleCamera: state.toggleCamera,
    setSelectedCameras: state.setSelectedCameras,
    loadNeutralLandmarks: state.loadNeutralLandmarks,
    startEarAddMode: state.startEarAddMode,
    cancelEarAddMode: state.cancelEarAddMode,
    recordEarClick: state.recordEarClick,
    clearEarClicks: state.clearEarClicks,
    saveEarPoints: state.saveEarPoints
  }));

  useEffect(() => {
    if (!neutral) return;
    for (const cameraId of selectedCameras) void loadNeutralLandmarks(cameraId);
  }, [neutral, selectedCameras, loadNeutralLandmarks]);

  const camerasBySide = useMemo(() => {
    const groups: Partial<Record<Ava256NeutralCamera["side"], Ava256NeutralCamera[]>> = {};
    for (const camera of neutral?.cameras ?? []) (groups[camera.side] ??= []).push(camera);
    return groups;
  }, [neutral]);

  if (!selectedCapture) return null;
  if (isNeutralLoading) return <div className="text-slate-400">Loading the neutral frame...</div>;
  if (neutralError || !neutral) {
    return (
      <div className="space-y-2 text-sm">
        <div className="text-rose-300">Neutral frame unavailable: {neutralError ?? "not loaded"}</div>
        <button
          className="rounded border border-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:text-white"
          onClick={() => void loadNeutral(selectedCapture)}
        >
          Retry
        </button>
      </div>
    );
  }

  const buttonClass = "px-3 py-1.5 text-xs font-medium";
  const leftCount = Object.keys(earClicksLeft).length;
  const rightCount = Object.keys(earClicksRight).length;
  const canSave = leftCount >= 2 || rightCount >= 2;
  const canClear = leftCount > 0 || rightCount > 0;
  const modeButton = (ear: "left" | "right", label: string) => (
    <button
      className={`${buttonClass} rounded border ${
        earAddMode === ear
          ? "border-amber-400 bg-amber-500/20 text-amber-200"
          : "border-slate-700 text-slate-300 hover:text-white"
      }`}
      onClick={() => (earAddMode === ear ? cancelEarAddMode() : startEarAddMode(ear))}
    >
      {earAddMode === ear ? "Cancel Mode" : label}
    </button>
  );

  return (
    <section className="flex flex-col gap-4">
      <header>
        <h2 className="text-lg font-semibold text-slate-100 break-all">{selectedCapture}</h2>
        <p className="text-xs text-slate-400">
          Neutral frame {neutral.frame_id} (EXP_neutral_peak, keypoints {neutral.coverage}/{neutral.needed_total}) ·
          Left ear top (2): {neutral.ear_tops.includes(2) ? "saved" : "not saved"} · Right ear top (9):{" "}
          {neutral.ear_tops.includes(9) ? "saved" : "not saved"}
        </p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {modeButton("left", "Add Left Ear Top")}
          {modeButton("right", "Add Right Ear Top")}
          {(earAddMode || canClear) && (
            <>
              <button
                className={`${buttonClass} rounded border border-emerald-500 bg-emerald-500/20 text-emerald-200 disabled:opacity-40 disabled:cursor-not-allowed`}
                disabled={!canSave || isSavingEar}
                onClick={() => void saveEarPoints()}
              >
                {isSavingEar ? "Saving..." : "Save"}
              </button>
              <button
                className={`${buttonClass} rounded border border-slate-700 text-slate-300 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed`}
                disabled={!canClear || isSavingEar}
                onClick={() => clearEarClicks()}
              >
                Clear
              </button>
              <span className="text-xs text-slate-400">
                left: {leftCount} camera{leftCount === 1 ? "" : "s"} · right: {rightCount} camera
                {rightCount === 1 ? "" : "s"} (need at least 2 per side to save that side)
              </span>
            </>
          )}
        </div>
      </header>

      <details className="rounded-lg border border-slate-800 bg-slate-900/40 p-3 text-xs text-slate-300">
        <summary className="cursor-pointer select-none">
          Cameras: {selectedCameras.length} of {neutral.cameras.length} shown (cameras behind the head are left out)
        </summary>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            className="rounded border border-slate-700 px-2 py-1 hover:text-white"
            onClick={() => setSelectedCameras(neutral.default_cameras)}
          >
            Reset to preset
          </button>
          <button
            className="rounded border border-slate-700 px-2 py-1 hover:text-white"
            onClick={() => setSelectedCameras(neutral.cameras.map((c) => c.camera_id))}
          >
            Show all
          </button>
        </div>
        {SIDES.filter((side) => camerasBySide[side]?.length).map((side) => (
          <div key={side} className="mt-2">
            <div className="mb-1 text-slate-400">{SIDE_LABEL[side]}</div>
            <div className="flex flex-wrap gap-x-3 gap-y-1">
              {camerasBySide[side]!.map((camera) => (
                <label
                  key={camera.camera_id}
                  className="inline-flex items-center gap-1 tabular-nums"
                  title={`azimuth ${camera.azimuth_deg}°, elevation ${camera.elevation_deg}°`}
                >
                  <input
                    type="checkbox"
                    checked={selectedCameras.includes(camera.camera_id)}
                    onChange={() => toggleCamera(camera.camera_id)}
                  />
                  {camera.camera_id}
                </label>
              ))}
            </div>
          </div>
        ))}
      </details>

      <div className="grid multiview-grid gap-6 justify-items-center">
        {selectedCameras.map((cameraId) => (
          <ImageTile
            key={`${selectedCapture}:${cameraId}`}
            cameraId={cameraId}
            imageSrc={api.getAva256NeutralImageUrl(selectedCapture, cameraId)}
            frame={landmarkCache[`${selectedCapture}:${cameraId}`]}
            extraVisibleIndices={EXTRA_VISIBLE_INDICES}
            earAddMode={earAddMode !== null}
            pendingEarClick={
              earAddMode === "left" ? earClicksLeft[cameraId] : earAddMode === "right" ? earClicksRight[cameraId] : undefined
            }
            onEarClick={(x, y) => recordEarClick(cameraId, x, y)}
          />
        ))}
        {selectedCameras.length === 0 && (
          <div className="col-span-full rounded-lg border border-dashed border-slate-700 p-8 text-center text-slate-400">
            Select at least one camera above.
          </div>
        )}
      </div>
    </section>
  );
}
