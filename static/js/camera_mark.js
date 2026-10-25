// camera_mark.js
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

classSelect.addEventListener("change", () => {
  const option = classSelect.selectedOptions[0];
  recognizedIds = new Set();
  recognizedList.replaceChildren();
  if (sessionRecognizedBadge) sessionRecognizedBadge.innerText = "0";
  classAttendanceCount.innerText = option && option.value
    ? `Present for this class: ${option.dataset.attendedCount || 0}`
    : "Present for this class: choose a class";
  if (endLectureBtn) {
    endLectureBtn.style.display = option && option.value ? "inline-block" : "none";
  }
});

if (endLectureBtn) {
  endLectureBtn.addEventListener("click", async () => {
    const classId = classSelect.value;
    if (!classId) return;
    if (!confirm("End this lecture now and send attendance confirmation emails to all present students?")) {
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
      alert(data.message || "Lecture completed and attendance emails dispatched.");
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
    const selected = markCameraSelect.selectedOptions[0]?.textContent || "selected webcam";
    markStatus.innerText = `Found ${cameras.length} camera(s). Selected: ${selected}. Choose your laptop webcam if needed.`;
  } catch (err) {
    markStatus.innerText = err.message || "Could not detect webcams.";
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
    markStatus.innerText = "Switched webcam. Scanning...";
  } catch (err) {
    markStatus.innerText = err.message || "Could not switch webcams.";
  }
});

startMarkBtn.addEventListener("click", async () => {
  if (!classSelect.value) {
    markStatus.innerText = "Choose a class before starting attendance.";
    return;
  }
  startMarkBtn.disabled = true;
  stopMarkBtn.disabled = false;
  classSelect.disabled = true;
  try {
    if (!markCameraSelect.value) await CameraPicker.detect(markCameraSelect);
    markStream = await CameraPicker.open(markCameraSelect);
    markVideo.srcObject = markStream;
    await markVideo.play();
    scanning = true;
    markStatus.innerText = "Scanning...";
    captureAndRecognize();
  } catch (err) {
    alert("Camera error: " + err.message);
    startMarkBtn.disabled = false;
    stopMarkBtn.disabled = true;
    classSelect.disabled = false;
  }
});

stopMarkBtn.addEventListener("click", () => {
  scanning = false;
  if (markStream) markStream.getTracks().forEach(t => t.stop());
  startMarkBtn.disabled = false;
  stopMarkBtn.disabled = true;
  classSelect.disabled = false;
  markStatus.innerText = "Stopped";
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
    if (!blob) throw new Error("Could not capture a camera frame.");
    const fd = new FormData();
    fd.append("image", blob, "snap.jpg");
    fd.append("class_id", classSelect.value);
    const res = await fetch("/recognize_face", {
      method: "POST",
      headers: { "X-CSRFToken": csrfToken },
      body: fd
    });
    const j = await res.json();
    if (j.recognized) {
      if (j.present_count !== null) {
        const option = classSelect.selectedOptions[0];
        option.dataset.attendedCount = j.present_count;
        classAttendanceCount.innerText = `Present for this class: ${j.present_count}`;
      }
      markStatus.innerText = j.already_marked
        ? `${j.name} is already marked present today`
        : `Attendance marked: ${j.name} (similarity ${j.similarity.toFixed(2)})`;
      const attendanceKey = `${j.class_id}:${j.student_id}`;
      if (!recognizedIds.has(attendanceKey)) {
        recognizedIds.add(attendanceKey);
        if (sessionRecognizedBadge) sessionRecognizedBadge.innerText = recognizedIds.size;
        const li = document.createElement("li");
        li.className = "list-group-item d-flex justify-content-between align-items-center";
        li.innerHTML = `<span><strong>${j.name}</strong></span> <span class="badge bg-success rounded-pill">${new Date().toLocaleTimeString()}</span>`;
        recognizedList.prepend(li);
      }
    } else {
      if (j.error) markStatus.innerText = `Not recognized: ${j.error}`;
      else markStatus.innerText = `Not recognized`;

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

      if (j.attendance_window_closed) {
        scanning = false;
        if (markStream) markStream.getTracks().forEach(track => track.stop());
        markStream = null;
        markVideo.srcObject = null;
        startMarkBtn.disabled = true;
        stopMarkBtn.disabled = true;
        classSelect.disabled = false;
        markStatus.innerText = j.error;
      }
    }
  } catch (err) {
    markStatus.innerText = err.message || "Recognition request failed.";
  } finally {
    if (scanning) setTimeout(captureAndRecognize, 1200);
  }
}
