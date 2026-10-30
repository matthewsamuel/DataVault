// Stijlgids: dezelfde inhoud in een lichte en een donkere kolom (styleguide.html)
document.querySelectorAll('.sg-column').forEach((column, i) => {
    column.appendChild(document.getElementById('sg-content').content.cloneNode(true));
    column.querySelectorAll('input[type="radio"]').forEach(radio => { radio.name = `sg-radio-${i}`; });
});

// Beweging: de veren, het ritme, een teller en FLIP (motion.js)
document.querySelectorAll('.sg-motion').forEach(section => {
    const play = () => {
        section.querySelectorAll('.sg-spring-dot').forEach(dot => {
            const track = dot.parentElement.getBoundingClientRect().width - dot.getBoundingClientRect().width - 2 * Motion.px('--space-xs', 4);
            Motion.spring(dot, [{ transform: `translateX(${-track}px)` }, { transform: 'none' }], dot.dataset.spring);
        });
        Motion.stagger(section.querySelectorAll('.sg-stagger .thumb-tile'), 'rise');
        const count = section.querySelector('.sg-count');
        Motion.countUp(count, Number(count.dataset.to), { from: 0 });
    };
    section.querySelector('.sg-motion-play').addEventListener('click', play);
    section.querySelector('.sg-motion-flip').addEventListener('click', () => {
        const box = section.querySelector('.sg-stagger');
        const tiles = [...box.children];
        Motion.flip(tiles, () => {
            tiles.sort(() => Math.random() - 0.5).forEach(tile => box.appendChild(tile));
        });
    });
});
