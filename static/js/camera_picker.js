window.CameraPicker = {
  async detect(select) {
    let devices = await navigator.mediaDevices.enumerateDevices();
    if (!devices.some(device => device.kind === "videoinput" && device.label)) {
      const permissionStream = await navigator.mediaDevices.getUserMedia({ video: true });
      permissionStream.getTracks().forEach(track => track.stop());
      devices = await navigator.mediaDevices.enumerateDevices();
    }

    const cameras = devices.filter(device => device.kind === "videoinput");
    if (!cameras.length) throw new Error("No webcam was found. Connect or enable the laptop webcam.");

    if (select.dataset.selectionListener !== "true") {
      select.addEventListener("change", () => {
        select.dataset.manualSelection = "true";
      });
      select.dataset.selectionListener = "true";
    }
    const previous = select.value;
    const manuallySelected = select.dataset.manualSelection === "true";
    const blocked = /phone|mobile|link to windows|droidcam|iriun|virtual|obs camera/i;
    const builtIn = /integrated|built.?in|internal|laptop|webcam/i;
    const preferred = cameras.find(camera => !blocked.test(camera.label) && builtIn.test(camera.label))
      || cameras.find(camera => !blocked.test(camera.label))
      || cameras[0];

    select.replaceChildren();
    cameras.forEach((camera, index) => {
      const option = document.createElement("option");
      option.value = camera.deviceId;
      const label = camera.label || `Camera ${index + 1}`;
      option.textContent = camera.deviceId === preferred.deviceId
        ? `${label} (recommended)`
        : label;
      select.append(option);
    });
    select.value = manuallySelected && cameras.some(camera => camera.deviceId === previous)
      ? previous
      : preferred.deviceId;
    select.disabled = false;
    return cameras;
  },

  async open(select) {
    if (!select.value) {
      try {
        await this.detect(select);
      } catch (e) {
        console.warn("Auto-detect failed, using default camera constraints:", e);
      }
    }
    const constraints = {
      audio: false,
      video: select.value
        ? { deviceId: { ideal: select.value }, width: { ideal: 640 }, height: { ideal: 480 } }
        : { width: { ideal: 640 }, height: { ideal: 480 } }
    };
    try {
      return await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      console.warn("Ideal camera constraints failed, attempting fallback to basic video stream...", err);
      return await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    }
  }
};
