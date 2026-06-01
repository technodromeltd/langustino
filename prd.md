# Langustino PRD

## Product

Langustino is a mobile-first PWA for learning languages quickly through high-frequency vocabulary, spaced retention, and satisfying daily practice loops.

Initial language path:

- Learn Spanish from English.
- First content set: 1000 most common Spanish words.

The product promise is simple: open the app, complete one focused daily session, and steadily turn the most useful words into long-term memory.

## Goals

- Help English speakers learn the 1000 most common Spanish words.
- Make daily practice feel fast, clear, and rewarding on a phone.
- Use spaced retention so learned words come back at the right time.
- Support both directions: English to Spanish and Spanish to English.
- Keep content separate from app logic so new languages and sets can be added later.
- Store user progress locally in the browser for offline-first use.

## Non-Goals For MVP

- User accounts, cloud sync, or social features.
- Speech recognition.
- AI-generated lessons.
- Grammar course content beyond card details.
- Payments or subscriptions.
- Teacher/admin tools.

## Target User

An English-speaking learner who wants to start Spanish with practical vocabulary. They use the app during small pockets of time, usually on a phone, and need the experience to feel effortless enough to repeat daily.

## Core Experience

The first screen should take the user directly into practice or clearly show today's session. The app should avoid feeling like a dashboard-first productivity tool. It should feel like a calm, polished learning loop:

1. Open app.
2. See daily goal and streak.
3. Practice 50 cards.
4. Get immediate feedback.
5. End with progress, streak, and a clear reason to return tomorrow.

## MVP Features

### 1. Content Set: 1000 Common Spanish Words

The first set contains 1000 high-frequency Spanish words for English speakers.

Each card should include:

- Spanish word or phrase.
- English translation.
- Part of speech.
- Short example sentence when useful.
- Optional pronunciation hint.
- Optional notes for common confusion.
- Verb conjugation details when the word is a verb.

Card detail mode for verbs should include common beginner-useful conjugations:

- Infinitive.
- Present tense.
- Preterite or simple past when useful.
- Common irregular forms.
- Short usage note when the conjugation is likely to confuse learners.

### 2. Memrise-Style Flipcards

Cards should feel quick and tactile.

Basic card flow:

1. Show prompt side.
2. User thinks of answer.
3. User taps to flip.
4. Show answer side.
5. User marks recall quality.

Recall quality buttons:

- Forgot.
- Hard.
- Good.
- Easy.

The card should support two directions:

- English to Spanish.
- Spanish to English.

The app should mix both directions over time. MVP can use a simple setting or weighted default:

- New cards first appear English to Spanish.
- Review cards may appear in either direction.
- A card is considered stronger only when both directions are improving.

### 3. Spaced Retention System

The app should schedule reviews based on recall quality.

MVP scheduling can be simple and transparent:

- Forgot: repeat soon in the same session, then review tomorrow.
- Hard: review tomorrow or in 2 days.
- Good: increase interval.
- Easy: increase interval more aggressively.

Each user-card-direction pair should track:

- Current interval.
- Ease score.
- Due date.
- Review count.
- Lapse count.
- Last reviewed date.
- Last recall quality.

The system should treat English to Spanish and Spanish to English as related but separate memory traces. A user may know one direction better than the other.

### 4. Daily Practice

Default daily session:

- 50 words per day.
- 35 new words.
- 15 repetitions.

Daily session behavior:

- If fewer than 15 review cards are due, fill remaining slots with new cards up to the 50-card cap.
- If more than 15 review cards are due, prioritize due reviews before adding new words.
- If the learner has seen all 1000 words, the session becomes review-only.
- Forgotten cards can reappear inside the same session without counting as new daily words.

The daily plan should be generated from local progress state and content metadata.

### 5. Streak

Track daily completion streak.

MVP streak rule:

- A streak day is counted when the user completes the daily session.
- The streak continues if the previous completed day was yesterday.
- Completing more practice on the same day should not increment streak again.

Nice-to-have after MVP:

- One streak freeze.
- Weekly streak calendar.
- Gentle recovery state after a missed day.

### 6. Stats

Stats should answer:

- How many words have I started?
- How many words have I learned well?
- Which words are weak?
- How consistent am I?

MVP stats:

- Total words in set.
- New words seen.
- Words currently learning.
- Words learned.
- Words mastered.
- Reviews completed.
- Accuracy by recall quality.
- Current streak.
- Best streak.
- Due reviews today.

Suggested learning buckets:

- New: never reviewed.
- Seen: introduced but not stable.
- Learning: reviewed, short interval.
- Learned: stable interval above threshold.
- Mastered: strong in both directions with long interval.

### 7. PWA And Local Storage

The app should be installable as a PWA and optimized for mobile phones.

PWA requirements:

- Installable on iOS and Android.
- Offline-capable after first load.
- App shell cached.
- Content set cached.
- Local progress available offline.
- Fast cold start.

User data:

- Store progress in local browser storage for MVP.
- Use a versioned storage schema.
- Content data should be read-only static app data.
- User progress should reference content IDs, not duplicate the content.

Preferred storage split:

- Static content: bundled JSON files or static assets.
- User progress: localStorage for MVP, with an IndexedDB migration path if needed.
- App settings: localStorage.

Important local data:

- Active set ID.
- Daily session state.
- Per-card progress.
- Per-direction progress.
- Streak history.
- Stats summary cache.
- App settings.

The app should include a future-friendly export/import path for local progress, even if the UI is not included in MVP.

## Content And Logic Separation

The app must not hardcode Spanish-specific learning logic into the practice engine.

Content set responsibilities:

- Define cards.
- Define source language and target language.
- Define translations and examples.
- Define card detail metadata.
- Define tags, levels, and ordering.

App logic responsibilities:

- Daily session generation.
- Spaced retention scheduling.
- Progress tracking.
- Stats.
- UI rendering.
- Settings.

Example content set shape:

```json
{
  "id": "spanish-1000-common-en",
  "title": "1000 Most Common Spanish Words",
  "sourceLanguage": "en",
  "targetLanguage": "es",
  "version": 1,
  "cards": [
    {
      "id": "es-0001",
      "rank": 1,
      "type": "verb",
      "target": "ser",
      "source": "to be",
      "partOfSpeech": "verb",
      "examples": [
        {
          "target": "Soy estudiante.",
          "source": "I am a student."
        }
      ],
      "details": {
        "conjugations": {
          "present": {
            "yo": "soy",
            "tu": "eres",
            "el_ella_usted": "es",
            "nosotros": "somos",
            "ellos_ustedes": "son"
          }
        },
        "notes": ["Used for identity, origin, and permanent traits."]
      },
      "tags": ["common", "irregular", "beginner"]
    }
  ]
}
```

Example user progress shape:

```json
{
  "schemaVersion": 1,
  "activeSetId": "spanish-1000-common-en",
  "cards": {
    "es-0001": {
      "introducedAt": "2026-06-01",
      "directions": {
        "source_to_target": {
          "intervalDays": 1,
          "ease": 2.5,
          "dueDate": "2026-06-02",
          "reviewCount": 1,
          "lapseCount": 0,
          "lastQuality": "good"
        },
        "target_to_source": {
          "intervalDays": 0,
          "ease": 2.5,
          "dueDate": "2026-06-01",
          "reviewCount": 0,
          "lapseCount": 0,
          "lastQuality": null
        }
      }
    }
  },
  "streak": {
    "current": 1,
    "best": 1,
    "lastCompletedDate": "2026-06-01"
  }
}
```

## UX Principles

- Practice first. The app should open to what the user should do next.
- One-handed friendly. Primary actions should be reachable on mobile.
- Fast feedback. Every answer should feel acknowledged instantly.
- Calm motivation. Streaks and stats should encourage without shaming.
- Low friction. No account required for MVP.
- Clear recovery. Users should be able to make mistakes and keep moving.
- Delight with restraint. Animations should support progress, not slow it down.

## Mobile UI Requirements

Core screens:

- Today practice.
- Flipcard practice.
- Session complete.
- Stats.
- Set overview.
- Settings.

Practice screen:

- Large prompt.
- Tap or swipe to flip.
- Clear answer reveal.
- Four recall buttons after reveal.
- Progress indicator for today's session.
- Quick access to details.

Card detail panel:

- Translation.
- Example sentence.
- Part of speech.
- Conjugations for verbs.
- Notes and tags.

Session complete screen:

- Daily session completed.
- Streak updated.
- New words learned today.
- Reviews completed.
- Weak words to expect again.
- Light celebratory animation.

Stats screen:

- Set progress.
- Learned and mastered counts.
- Recall quality trends.
- Streak.
- Due reviews.
- Weakest words.

## Animation And Feel

Use animation where it makes the app feel responsive and alive:

- Card flip animation.
- Button press feedback.
- Small progress movement after each answer.
- Gentle completion celebration.
- Streak increment emphasis.
- Smooth transitions between session states.

Avoid animations that:

- Delay the next card.
- Distract from recall.
- Make the app feel heavy on low-end phones.
- Hide important feedback.

Suggested interaction details:

- Haptic feedback where supported.
- Swipe gestures as optional accelerators.
- Reduced motion support.
- Instant keyboard-free operation.

## Accessibility

MVP should include:

- Large readable text.
- High contrast.
- Proper button labels.
- No color-only correctness indicators.
- Reduced motion mode.
- Touch targets at least 44px.
- Works in portrait orientation on small phones.

## Success Metrics

Product success:

- User completes first session.
- User returns the next day.
- User reaches a 7-day streak.
- User learns at least 100 words.
- User continues after missing a day.

Learning success:

- Recall improves across repeated reviews.
- More words move from learning to learned.
- Both directions improve over time.

Technical success:

- App works offline after first load.
- Progress persists across refreshes.
- Session state survives app close and reopen.
- Content set can be replaced or extended without rewriting the practice engine.

## Acceptance Criteria For MVP

- User can install or use the app as a mobile PWA.
- User can practice a daily 50-card session.
- Daily session includes a mix of new and review cards.
- User can flip cards and mark recall quality.
- Spaced retention updates due dates after every review.
- User progress persists locally after refresh.
- Streak updates after daily completion.
- Stats show progress and learned counts.
- Spanish 1000-word set is loaded as content data, not hardcoded UI logic.
- Cards support English to Spanish and Spanish to English.
- Verb cards can show conjugation details.

## Suggested Milestones

### Milestone 1: App Skeleton

- Mobile-first PWA shell.
- Static routing or app views.
- Local storage wrapper.
- Content set loader.
- Basic settings.

### Milestone 2: Content Model And First Cards

- Define content set schema.
- Add first sample Spanish cards.
- Render basic and detail card states.
- Validate that the practice engine reads content generically.

### Milestone 3: Practice Loop

- Flipcard interaction.
- Recall quality buttons.
- Session progress.
- Same-session forgotten-card repeat.
- Basic animations.

### Milestone 4: Spaced Retention And Daily Plan

- Per-card-direction progress.
- Due card selection.
- 35 new and 15 review target.
- Scheduling updates.
- Session resume after refresh.

### Milestone 5: Streaks And Stats

- Daily completion tracking.
- Current and best streak.
- Learned and mastered buckets.
- Stats screen.

### Milestone 6: Full Spanish 1000 Set

- Add and validate 1000-word content set.
- Add verb details and conjugations where useful.
- Add examples for high-value cards.
- Run content integrity checks.

### Milestone 7: Polish And PWA Readiness

- Installable manifest.
- Offline caching.
- Mobile polish.
- Reduced motion.
- Final onboarding-light first-run experience.

## Open Questions

- Should the first session start immediately, or should the user choose daily goal first?
- Should Spanish accents be required in typed answers later, or should MVP stay self-graded only?
- Should the app count "words" or "cards" when one word has multiple meanings?
- Should both directions appear from day one, or should target-to-source appear after initial exposure?
- Which source should be used to validate the 1000 most common Spanish words?
- Should local progress export/import be included in MVP UI or kept as a hidden technical capability?
