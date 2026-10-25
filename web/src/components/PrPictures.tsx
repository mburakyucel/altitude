import { useState } from "react";
import type { PrImages } from "../data/api";
import { useGallery } from "./MessageImages";
import { Icon, SavedImage } from "./Shown";
import "./shown.css";

type Side = "before" | "after";
type Picture = PrImages["pictures"][number];
const FIRST = 3;

/**
 * The pictures a held PR's commit adds, replaces or removes, stacked on its merge card (SPEC.md §3.8.6): each fitted
 * whole in its frame over its path, a replaced one with a Before / After switch. A picture opens the gallery.
 */
export function PrPictures({ pictures }: { pictures: PrImages }) {
  const gallery = useGallery();
  const [sides, setSides] = useState<Record<string, Side>>({});
  const [all, setAll] = useState(false);
  if (!pictures.available) {
    return <div className="shown-card" data-gone>
      <span className="shown-card-body">
        <span className="shown-card-title"><Icon name="image-off" /><b>Pictures in this PR</b></span>
        <span className="shown-card-excerpt">Preview unavailable</span>
      </span>
    </div>;
  }
  const side = (picture: Picture): Side => picture.before && picture.after ? sides[picture.path] ?? "after" : picture.after ? "after" : "before";
  const items = pictures.pictures;
  const open = (index: number) => gallery(items.map((picture) => {
    const shown = side(picture);
    const image = picture[shown]!;
    // The side leads the bar's second line, so it survives a long name.
    return { key: image.url, name: picture.name, url: image.url, width: image.width, height: image.height,
      context: `${picture.change === "changed" ? shown === "before" ? "Before" : "After" : picture.after ? "New" : "Removed"} · Pictures in this PR` };
  }), index);
  const more = items.length - FIRST;
  return <section className="pr-pictures" aria-label="Pictures in this PR">
    <p className="pr-pictures-label">Pictures in this PR</p>
    {(all ? items : items.slice(0, FIRST)).map((picture, index) => {
      const shown = side(picture);
      const image = picture[shown]!;
      return <figure key={picture.path} className="pr-picture" data-change={picture.change}>
        <SavedImage key={image.url} image={{ ...image, name: picture.name }}
          alt={`${picture.name}, ${picture.change === "changed" ? shown : picture.change}`} className="pr-picture-frame" open={() => open(index)} />
        <figcaption>
          <span className="pr-picture-path" title={picture.path}><bdi>{picture.path}</bdi></span>
          {picture.before && picture.after ? <span className="pr-picture-sides" role="group" aria-label={`Before or after: ${picture.name}`}>
            {(["before", "after"] as const).map((option) => <button key={option} type="button" aria-pressed={shown === option}
              onClick={() => setSides((old) => ({ ...old, [picture.path]: option }))}>{option === "before" ? "Before" : "After"}</button>)}
          </span> : <span className="shown-chip">{picture.after ? "New" : "Removed"}</span>}
        </figcaption>
      </figure>;
    })}
    {!all && more > 0 ? <button type="button" className="btn pr-pictures-more" onClick={() => setAll(true)}>
      Show {more} more picture{more === 1 ? "" : "s"}
    </button> : null}
    {pictures.not_shown.length ? <p className="pr-pictures-also">Also changed: {pictures.not_shown.join(", ")}</p> : null}
  </section>;
}
