// camera_mark.js - BioBytes Live Attendance Scanner
const startMarkBtn = document.getElementById("startMarkBtn");
const stopMarkBtn = document.getElementById("stopMarkBtn");
const endLectureBtn = document.getElementById("endLectureBtn");
const markVideo = document.getElementById("markVideo");
const markStatus = document.getElementById("markStatus");
const recognizedList = document.getElementById("recognizedList");
const sessionRecognizedBadge = document.getElementById("sessionRecognizedBadge");
const unknownFacesContainer = document.getElementById("unknownFacesContainer");
const unknownCountBadge = document.getElementById("unknownCountBadge");
const noUnknownMsg = document.getElementById("noUnknownMsg");
const detectMarkCameraBtn = document.getElementById("detectMarkCameraBtn");
const markCameraSelect = document.getElementById("markCameraSelect");
const classSelect = document.getElementById("classSelect");
const classAttendanceCount = document.getElementById("classAttendanceCount");

let markStream = null;
let scanning = false;
let recognizedIds = new Set();
let unknownCount = 0;
let lastUnknownSnapshotTime = 0;
const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

function updateClassInfo() {
  const option = classSelect.selectedOptions[0];
  const classVal = classSelect.value;
  if (classAttendanceCount) {
    if (classVal && classVal !== "0") {
      classAttendanceCount.innerHTML = `Present for this lecture: <strong class="text-dark">${option?.dataset?.attendedCount || 0}</strong>`;
    } else {
      classAttendanceCount.innerHTML = `Session mode: <strong class="text-primary">General Attendance (All Students)</strong>`;
    }
  }
  if (endLectureBtn) {
    endLectureBtn.style.display = (classVal && classVal !== "0") ? "inline-block" : "none";
  }
}

classSelect.addEventListener("change", () => {
  recognizedIds = new Set();
  recognizedList.replaceChildren();
  if (sessionRecognizedBadge) sessionRecognizedBadge.innerText = "0";
  updateClassInfo();
  startMarkBtn.disabled = false;
});

// Initialize class info on page load
updateClassInfo();

if (endLectureBtn) {
  endLectureBtn.addEventListener("click", async () => {
    const classId = classSelect.value;
    if (!classId || classId === "0") return;
    if (!confirm("End this lecture session now and dispatch email receipts to all present students?")) {
      return;
    }
    endLectureBtn.disabled = true;
    try {
      const res = await fetch(`/classes/${classId}/complete`, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest"
        }
      });
      const data = await res.json();
      alert(data.message || "Lecture completed and attendance confirmation emails dispatched!");
      if (scanning) {
        stopMarkBtn.click();
      }
    } catch (err) {
      alert("Could not complete lecture: " + err.message);
    } finally {
      endLectureBtn.disabled = false;
    }
  });
}

detectMarkCameraBtn.addEventListener("click", async () => {
  detectMarkCameraBtn.disabled = true;
  try {
    const cameras = await CameraPicker.detect(markCameraSelect);
    const selected = markCameraSelect.selectedOptions[0]?.textContent || "Selected Webcam";
    markStatus.innerHTML = `<span class="badge bg-info text-dark">Found ${cameras.length} camera(s)</span> Active: <strong>${selected}</strong>`;
  } catch (err) {
    markStatus.innerHTML = `<span class="badge bg-warning text-dark">Camera Note</span> ${err.message || "Could not detect webcams."}`;
  } finally {
    detectMarkCameraBtn.disabled = false;
  }
});

markCameraSelect.addEventListener("change", async () => {
  if (!scanning) return;
  try {
    const nextStream = await CameraPicker.open(markCameraSelect);
    const previousStream = markStream;
    markStream = nextStream;
    markVideo.srcObject = nextStream;
    await markVideo.play();
    if (previousStream) previousStream.getTracks().forEach(track => track.stop());
    markStatus.innerHTML = `<span class="badge bg-success">Switched Webcam</span> Scanning active...`;
  } catch (err) {
    markStatus.innerText = err.message || "Could not switch webcams.";
  }
});

startMarkBtn.addEventListener("click", async () => {
  startMarkBtn.disabled = true;
  stopMarkBtn.disabled = false;
  classSelect.disabled = true;
  markStatus.innerHTML = `<span class="spinner-border spinner-border-sm me-1" role="status"></span> Initializing camera...`;

  try {
    if (!markCameraSelect.value) {
      await CameraPicker.detect(markCameraSelect);
    }
    markStream = await CameraPicker.open(markCameraSelect);
    markVideo.srcObject = markStream;
    await markVideo.play();
    scanning = true;
    markStatus.innerHTML = `<span class="badge bg-success">Live Scanning</span> Point face at camera to mark attendance...`;
    captureAndRecognize();
  } catch (err) {
    markStatus.innerHTML = `<span class="badge bg-danger">Camera Error</span> ${err.message || "Could not start camera"}`;
    startMarkBtn.disabled = false;
    stopMarkBtn.disabled = true;
    classSelect.disabled = false;
  }
});

stopMarkBtn.addEventListener("click", () => {
  scanning = false;
  if (markStream) {
    markStream.getTracks().forEach(t => t.stop());
    markStream = null;
  }
  markVideo.srcObject = null;
  startMarkBtn.disabled = false;
  stopMarkBtn.disabled = true;
  classSelect.disabled = false;
  markStatus.innerHTML = `<span class="badge bg-secondary">Camera Paused / Stopped</span> Click 'Start Camera' to resume scanning.`;
});

async function captureAndRecognize() {
  if (!scanning) return;
  const canvas = document.createElement("canvas");
  canvas.width = markVideo.videoWidth || 640;
  canvas.height = markVideo.videoHeight || 480;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(markVideo, 0, 0, canvas.width, canvas.height);

  try {
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.85));
    if (!blob) throw new Error("Could not capture frame from webcam.");

    const fd = new FormData();
    fd.append("image", blob, "snap.jpg");
    fd.append("class_id", classSelect.value || "0");

    const res = await fetch("/recognize_face", {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken },
      body: fd
    });
    const j = await res.json();

    if (j.recognized) {
      if (j.present_count !== null && j.present_count !== undefined) {
        const option = classSelect.selectedOptions[0];
        if (option) option.dataset.attendedCount = j.present_count;
        if (classAttendanceCount) {
          classAttendanceCount.innerHTML = `Present for this session: <strong class="text-success">${j.present_count}</strong>`;
        }
      }

      if (j.already_marked) {
        markStatus.innerHTML = `<span class="badge bg-warning text-dark me-1">Already Marked</span> <strong>${j.name}</strong> is already marked present for this session.`;
      } else {
        markStatus.innerHTML = `<span class="badge bg-success me-1">Verified</span> Attendance recorded: <strong>${j.name}</strong> (Confidence: ${(j.similarity * 100).toFixed(0)}%)`;
      }

      const attendanceKey = `${j.class_id || 0}:${j.student_id}`;
      if (!recognizedIds.has(attendanceKey)) {
        recognizedIds.add(attendanceKey);
        if (sessionRecognizedBadge) sessionRecognizedBadge.innerText = recognizedIds.size;
        const li = document.createElement("li");
        li.className = "list-group-item d-flex justify-content-between align-items-center";
        li.innerHTML = `<span><strong>${j.name}</strong></span> <span class="badge bg-success rounded-pill">${new Date().toLocaleTimeString()}</span>`;
        recognizedList.prepend(li);
      }
    } else {
      if (j.error) {
        markStatus.innerHTML = `<span class="badge bg-secondary me-1">Scanning</span> ${j.error}`;
      } else {
        markStatus.innerHTML = `<span class="badge bg-secondary me-1">Scanning</span> Looking for registered face...`;
      }

      if (j.unknown_face) {
        const now = Date.now();
        if (now - lastUnknownSnapshotTime > 3500) {
          lastUnknownSnapshotTime = now;
          unknownCount++;
          if (unknownCountBadge) unknownCountBadge.innerText = unknownCount;
          if (noUnknownMsg) noUnknownMsg.style.display = "none";

          const snapDataUrl = canvas.toDataURL("image/jpeg", 0.65);
          const item = document.createElement("div");
          item.className = "card p-1 shadow-sm text-center border-danger flex-shrink-0";
          item.style.width = "76px";
          item.innerHTML = `
            <img src="${snapDataUrl}" class="rounded" style="width: 68px; height: 50px; object-fit: cover;">
            <span class="badge bg-danger mt-1" style="font-size: 8px;">Unknown</span>
            <span class="text-muted" style="font-size: 8px;">${new Date().toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'})}</span>
          `;
          if (unknownFacesContainer) {
            unknownFacesContainer.prepend(item);
            if (unknownFacesContainer.children.length > 8) {
              unknownFacesContainer.removeChild(unknownFacesContainer.lastChild);
            }
          }
        }
      }
    }
  } catch (err) {
    markStatus.innerText = err.message || "Recognition connection error.";
  } finally {
    if (scanning) {
      setTimeout(captureAndRecognize, 1200);
    }
  }
}
