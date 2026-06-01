import { availableSets } from "../src/content/registry";

for (const set of availableSets) {
  const ids = new Set<string>();
  const ranks = new Set<number>();

  if (!set.cards.length) {
    throw new Error(`${set.id} has no cards`);
  }

  for (const card of set.cards) {
    if (ids.has(card.id)) {
      throw new Error(`Duplicate card id ${card.id} in ${set.id}`);
    }

    if (ranks.has(card.rank)) {
      throw new Error(`Duplicate card rank ${card.rank} in ${set.id}`);
    }

    if (!card.id || !card.source || !card.target || !card.partOfSpeech) {
      throw new Error(`Card ${card.id || "(missing id)"} is missing required text`);
    }

    if (card.type === "verb" && !card.details?.conjugations) {
      throw new Error(`Verb card ${card.id} is missing conjugations`);
    }

    ids.add(card.id);
    ranks.add(card.rank);
  }
}

const cardCount = availableSets.reduce((count, set) => count + set.cards.length, 0);

console.log(`Validated ${availableSets.length} content set(s), ${cardCount} card(s).`);
