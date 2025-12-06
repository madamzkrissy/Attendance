// camera.js - lightweight camera capture and POST helpers
let videoStream;
async function startVideo() {
  const video = document.getElementById('video');
  try {
    videoStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
    video.srcObject = videoStream;
  } catch (e) {
    console.error('camera error', e);
    alert('Unable to access camera.');
  }
}

function captureToDataURL() {
  const video = document.getElementById('video');
  const canvas = document.createElement('canvas');
  canvas.width = video.videoWidth || 640;
  canvas.height = video.videoHeight || 480;
  const ctx = canvas.getContext('2d');
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  return canvas.toDataURL('image/jpeg');
}

document.addEventListener('DOMContentLoaded', ()=>{
  const vid = document.getElementById('video');
  if (vid) startVideo();

  const captureBtn = document.getElementById('capture');
  if (captureBtn) {
    captureBtn.addEventListener('click', ()=>{
      const data = captureToDataURL();
      const imgField = document.getElementById('imageField');
      if (imgField) imgField.value = data;
      alert('Captured. Now submit the form.');
    });
  }

  const scanBtn = document.getElementById('scanBtn');
  if (scanBtn) {
    scanBtn.addEventListener('click', async ()=>{
      const status = document.getElementById('status');
      status.innerText = 'Capturing...';
      const data = captureToDataURL();
      status.innerText = 'Sending to server...';
      try {
        const res = await fetch('/api/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({image:data})});
        const j = await res.json();
        if (!j.ok) {
          status.innerText = 'Error: ' + (j.error || JSON.stringify(j));
          return;
        }
        // show first result
        const r = j.results && j.results[0];
        if (!r || r.status==='unknown') {
          status.innerHTML = '<b>Unknown</b> - face not recognized. Try again.';
        } else {
          status.innerHTML = `<b>${r.name}</b> - ${r.status}`;
        }
      } catch (e) {
        status.innerText = 'Error scanning: ' + e;
      }
    });
  }

  const retryBtn = document.getElementById('retryBtn');
  if (retryBtn) {
    retryBtn.addEventListener('click', ()=>{
      const status = document.getElementById('status');
      status.innerText = 'Ready';
    });
  }
});
