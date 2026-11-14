/**
 * Chart Component Renderer
 *
 * Renders G2Plot charts from a chart spec produced by ChartComponentHandler.
 * This is the component-library equivalent of the original renderChart() in utils.js.
 *
 * Expects G2Plot to be loaded globally (CDN dependency declared in manifest.json).
 */

/**
 * Render a chart into a container element.
 *
 * @param {string} containerId - DOM element ID to render into
 * @param {object} payload - ComponentRenderPayload.spec from the backend
 *   Expected shape: { type: "Column"|"Line"|"Pie"|..., options: { data, xField, yField, ... } }
 */
export function renderChart(containerId, payload) {
    try {
        const spec = typeof payload === 'string' ? JSON.parse(payload) : payload;

        if (!spec || !spec.type || !spec.options) {
            throw new Error('Invalid chart specification provided.');
        }

        if (typeof G2Plot === 'undefined' || !G2Plot[spec.type]) {
            throw new Error(`Chart type "${spec.type}" is not supported or G2Plot is not loaded.`);
        }

        // Give the container a DEFINITE height before rendering. G2Plot defaults to
        // autoFit:true, whose ResizeObserver feeds back against a min-height-only
        // container and grows it without bound. A fixed height makes autoFit stable.
        const el = document.getElementById(containerId);
        if (el) {
            const h = (spec.options && Number(spec.options.height)) || 400;
            el.style.height = `${h}px`;
            el.style.maxHeight = `${h}px`;
            // Width tracks the (shrinking) chat column; autoFit's ResizeObserver
            // then refits the chart live when side panels open/close.
            el.style.width = '100%';
            el.style.maxWidth = '100%';
        }

        // autoFit keeps the chart sized to its container (the fixed height above
        // keeps it stable). Force it on so the chart is responsive to the column.
        if (spec.options && spec.options.autoFit !== false) spec.options.autoFit = true;
        // ONE fact, ONE place: inside an evidence row the row's summary already states the
        // chart's title (and its type and point count); the plot does not repeat it as a
        // heading. On a dashboard tile or a canvas the plot keeps its title.
        const _host = document.getElementById(containerId);
        if (spec.options && spec.options.title && _host && _host.closest && _host.closest('details.ans-ev')) {
            spec.options = { ...spec.options, title: undefined };
        }

        const plot = new G2Plot[spec.type](containerId, spec.options);
        plot.render();
        return plot;
    } catch (e) {
        console.error('Failed to render chart:', e);
        const container = document.getElementById(containerId);
        if (container) {
            container.innerHTML = `<div class="p-4 text-red-400">Error rendering chart: ${e.message}</div>`;
        }
        return null;
    }
}
