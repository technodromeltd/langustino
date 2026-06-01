import { availableSets } from "../src/content/registry";

for (const set of availableSets) {
  const ids = new Set<string>();
  const ranks = new Set<number>();

  if (!set.cards.length) {
    throw new Error(`${set.id} has no cards`);
  }

  if (set.id === "spanish-1000-common-en" && set.cards.length !== 1000) {
    throw new Error(`${set.id} should contain exactly 1000 cards, found ${set.cards.length}`);
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

    if (set.id === "spanish-1000-common-en" && (card.rank < 1 || card.rank > 1000)) {
      throw new Error(`Card ${card.id} has rank outside the top 1000`);
    }

    ids.add(card.id);
    ranks.add(card.rank);
  }

  if (set.id === "spanish-1000-common-en") {
    for (let rank = 1; rank <= 1000; rank += 1) {
      if (!ranks.has(rank)) {
        throw new Error(`${set.id} is missing rank ${rank}`);
      }
    }
  }
}

const cardCount = availableSets.reduce((count, set) => count + set.cards.length, 0);

console.log(`Validated ${availableSets.length} content set(s), ${cardCount} card(s).`);
