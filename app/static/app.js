// Ensure Alpine initializes HTMX-swapped content
document.addEventListener("htmx:afterSwap", () => {
    if (window.Alpine) Alpine.initTree(document.body);
});

// Open /applications in a new tab after logging an application
document.addEventListener("applicationCreated", () => {
    window.open("/applications", "_blank");
});
