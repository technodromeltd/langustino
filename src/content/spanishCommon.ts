import rows from "./spanishCommon1000.json";
import type { CardDetails, ContentCard, ContentSet, VerbConjugations } from "../core/content";

interface SpanishFrequencyRow {
  rank: number;
  article: string;
  word: string;
  translation: string;
  partOfSpeech: string;
}

const enrichedVerbs: Record<string, CardDetails> = {
  ser: {
    conjugations: {
      present: {
        yo: "soy",
        tu: "eres",
        el_ella_usted: "es",
        nosotros: "somos",
        ellos_ustedes: "son",
      },
      preterite: {
        yo: "fui",
        tu: "fuiste",
        el_ella_usted: "fue",
        nosotros: "fuimos",
        ellos_ustedes: "fueron",
      },
      notes: ["Used for identity, origin, time, and lasting traits."],
    },
    notes: ["One of two common Spanish verbs for 'to be'."],
  },
  estar: {
    conjugations: {
      present: {
        yo: "estoy",
        tu: "estas",
        el_ella_usted: "esta",
        nosotros: "estamos",
        ellos_ustedes: "estan",
      },
      preterite: {
        yo: "estuve",
        tu: "estuviste",
        el_ella_usted: "estuvo",
        nosotros: "estuvimos",
        ellos_ustedes: "estuvieron",
      },
      notes: ["Used for location, conditions, feelings, and temporary states."],
    },
    notes: ["One of two common Spanish verbs for 'to be'."],
  },
  haber: {
    conjugations: {
      present: {
        yo: "he",
        tu: "has",
        el_ella_usted: "ha",
        nosotros: "hemos",
        ellos_ustedes: "han",
      },
      notes: ["Usually used as an auxiliary verb, as in 'he comido'."],
    },
  },
  ir: {
    conjugations: {
      present: {
        yo: "voy",
        tu: "vas",
        el_ella_usted: "va",
        nosotros: "vamos",
        ellos_ustedes: "van",
      },
      preterite: {
        yo: "fui",
        tu: "fuiste",
        el_ella_usted: "fue",
        nosotros: "fuimos",
        ellos_ustedes: "fueron",
      },
    },
  },
  tener: {
    conjugations: {
      present: {
        yo: "tengo",
        tu: "tienes",
        el_ella_usted: "tiene",
        nosotros: "tenemos",
        ellos_ustedes: "tienen",
      },
      preterite: {
        yo: "tuve",
        tu: "tuviste",
        el_ella_usted: "tuvo",
        nosotros: "tuvimos",
        ellos_ustedes: "tuvieron",
      },
    },
  },
  saber: {
    conjugations: {
      present: {
        yo: "se",
        tu: "sabes",
        el_ella_usted: "sabe",
        nosotros: "sabemos",
        ellos_ustedes: "saben",
      },
      preterite: {
        yo: "supe",
        tu: "supiste",
        el_ella_usted: "supo",
        nosotros: "supimos",
        ellos_ustedes: "supieron",
      },
      notes: ["Use saber for facts and skills; conocer is for familiarity."],
    },
  },
  poder: {
    conjugations: {
      present: {
        yo: "puedo",
        tu: "puedes",
        el_ella_usted: "puede",
        nosotros: "podemos",
        ellos_ustedes: "pueden",
      },
      preterite: {
        yo: "pude",
        tu: "pudiste",
        el_ella_usted: "pudo",
        nosotros: "pudimos",
        ellos_ustedes: "pudieron",
      },
    },
  },
  querer: {
    conjugations: {
      present: {
        yo: "quiero",
        tu: "quieres",
        el_ella_usted: "quiere",
        nosotros: "queremos",
        ellos_ustedes: "quieren",
      },
      preterite: {
        yo: "quise",
        tu: "quisiste",
        el_ella_usted: "quiso",
        nosotros: "quisimos",
        ellos_ustedes: "quisieron",
      },
    },
  },
  hacer: {
    conjugations: {
      present: {
        yo: "hago",
        tu: "haces",
        el_ella_usted: "hace",
        nosotros: "hacemos",
        ellos_ustedes: "hacen",
      },
      preterite: {
        yo: "hice",
        tu: "hiciste",
        el_ella_usted: "hizo",
        nosotros: "hicimos",
        ellos_ustedes: "hicieron",
      },
    },
  },
  decir: {
    conjugations: {
      present: {
        yo: "digo",
        tu: "dices",
        el_ella_usted: "dice",
        nosotros: "decimos",
        ellos_ustedes: "dicen",
      },
      preterite: {
        yo: "dije",
        tu: "dijiste",
        el_ella_usted: "dijo",
        nosotros: "dijimos",
        ellos_ustedes: "dijeron",
      },
    },
  },
  ver: {
    conjugations: {
      present: {
        yo: "veo",
        tu: "ves",
        el_ella_usted: "ve",
        nosotros: "vemos",
        ellos_ustedes: "ven",
      },
      preterite: {
        yo: "vi",
        tu: "viste",
        el_ella_usted: "vio",
        nosotros: "vimos",
        ellos_ustedes: "vieron",
      },
    },
  },
  sentir: {
    conjugations: {
      present: {
        yo: "siento",
        tu: "sientes",
        el_ella_usted: "siente",
        nosotros: "sentimos",
        ellos_ustedes: "sienten",
      },
      notes: ["Stem changes from e to ie in most present-tense forms."],
    },
  },
  creer: {
    conjugations: regularConjugations("creer"),
  },
  parecer: {
    conjugations: {
      present: {
        yo: "parezco",
        tu: "pareces",
        el_ella_usted: "parece",
        nosotros: "parecemos",
        ellos_ustedes: "parecen",
      },
    },
  },
  mirar: {
    conjugations: regularConjugations("mirar"),
  },
  hablar: {
    conjugations: regularConjugations("hablar"),
  },
};

const cards: ContentCard[] = (rows as SpanishFrequencyRow[]).map((row) => ({
  id: `es-${String(row.rank).padStart(4, "0")}`,
  rank: row.rank,
  type: row.partOfSpeech === "verb" ? "verb" : "word",
  target: targetFor(row),
  source: row.translation,
  partOfSpeech: row.partOfSpeech,
  details: detailsFor(row),
  tags: tagsFor(row),
}));

export const spanishCommonWords: ContentSet = {
  id: "spanish-1000-common-en",
  title: "1000 Most Common Spanish Words",
  sourceLanguage: "en",
  targetLanguage: "es",
  version: 2,
  sourceNote: "Frequency ranks based on OPUS subtitle data via Adsonant CSV.",
  sourceUrl: "https://www.adsonant.com/resources/spanish/1000-most-common-words/",
  cards,
};

function targetFor(row: SpanishFrequencyRow) {
  return row.article ? `${row.article} ${row.word}` : row.word;
}

function detailsFor(row: SpanishFrequencyRow): CardDetails | undefined {
  if (row.partOfSpeech !== "verb") {
    return undefined;
  }

  return enrichedVerbs[row.word] ?? {
    conjugations: regularConjugations(row.word),
  };
}

function tagsFor(row: SpanishFrequencyRow) {
  return [
    "common",
    row.partOfSpeech,
    row.rank <= 100 ? "top-100" : row.rank <= 500 ? "top-500" : "top-1000",
  ];
}

function regularConjugations(infinitive: string): VerbConjugations {
  const stem = infinitive.slice(0, -2);
  const ending = infinitive.slice(-2);

  if (ending === "ar") {
    return {
      present: {
        yo: `${stem}o`,
        tu: `${stem}as`,
        el_ella_usted: `${stem}a`,
        nosotros: `${stem}amos`,
        ellos_ustedes: `${stem}an`,
      },
      notes: ["Regular -ar present-tense pattern."],
    };
  }

  if (ending === "er") {
    return {
      present: {
        yo: `${stem}o`,
        tu: `${stem}es`,
        el_ella_usted: `${stem}e`,
        nosotros: `${stem}emos`,
        ellos_ustedes: `${stem}en`,
      },
      notes: ["Regular -er present-tense pattern."],
    };
  }

  if (ending === "ir") {
    return {
      present: {
        yo: `${stem}o`,
        tu: `${stem}es`,
        el_ella_usted: `${stem}e`,
        nosotros: `${stem}imos`,
        ellos_ustedes: `${stem}en`,
      },
      notes: ["Regular -ir present-tense pattern."],
    };
  }

  return {
    present: {
      infinitive,
    },
    notes: ["Conjugation needs manual review."],
  };
}
