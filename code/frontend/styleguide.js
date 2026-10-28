// Stijlgids: dezelfde inhoud in een lichte en een donkere kolom (styleguide.html)
document.querySelectorAll('.sg-column').forEach((column, i) => {
    column.appendChild(document.getElementById('sg-content').content.cloneNode(true));
    column.querySelectorAll('input[type="radio"]').forEach(radio => { radio.name = `sg-radio-${i}`; });
});
