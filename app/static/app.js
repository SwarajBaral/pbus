document.addEventListener("htmx:afterRequest", ({ detail }) => {
  if (detail.requestConfig.verb === "get") return;
  document.getElementById("flash").textContent = detail.successful ? "" : errorMessage(detail.xhr);
});

function errorMessage(xhr) {
  try {
    const { detail } = JSON.parse(xhr.responseText);
    if (typeof detail === "string") return detail;
  } catch {}
  return xhr.status === 422 ? "Choose a CSV file first." : `Request failed (${xhr.status})`;
}
