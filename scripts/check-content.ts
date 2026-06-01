import { availableSets } from "../src/content/registry";

for (const set of availableSets) {
  const ids = new Set<string>();

  for (const card of set.cards) {
    if (ids.has(card.id)) {
      throw new Error(`Duplicate card id ${card.id} in ${set.id}`);
    }

    ids.add(card.id);
  }
}

console.log(`Validated ${availableSets.length} content set(s).`);
