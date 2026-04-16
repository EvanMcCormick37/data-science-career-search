// Ensure Alpine initializes HTMX-swapped content
document.addEventListener("htmx:afterSwap", () => {
    if (window.Alpine) Alpine.initTree(document.body);
});
