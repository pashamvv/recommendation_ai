BEGIN;

DELETE FROM users
WHERE email IN (
    'ai_action_tester@example.com',
    'ai_horror_tester@example.com'
);

INSERT INTO users (
    username,
    email,
    password_hash,
    role_id,
    created_at
)
VALUES
    (
        'ai_action_tester',
        'ai_action_tester@example.com',
        'pbkdf2_sha256$390000$ai_action_seed$SawFRPMLFabQR_ZglaacIkcW0jJZwQXTk_NkvLRE1MU=',
        2,
        NOW()
    ),
    (
        'ai_horror_tester',
        'ai_horror_tester@example.com',
        'pbkdf2_sha256$390000$ai_horror_seed$nbmN3SCGtUUsPvDvP9Gh6G1buP9EIG4i2APV2gbGk1w=',
        2,
        NOW()
    );

INSERT INTO favorites (user_id, movie_id, created_at)
SELECT u.user_id, seeded.movie_id, NOW()
FROM users u
JOIN (
    VALUES
        ('ai_action_tester@example.com', 10),
        ('ai_action_tester@example.com', 11),
        ('ai_action_tester@example.com', 59),
        ('ai_horror_tester@example.com', 1),
        ('ai_horror_tester@example.com', 38),
        ('ai_horror_tester@example.com', 42)
) AS seeded(email, movie_id)
    ON seeded.email = u.email;

INSERT INTO user_reactions (user_id, movie_id, reaction, created_at)
SELECT u.user_id, seeded.movie_id, seeded.reaction, NOW()
FROM users u
JOIN (
    VALUES
        ('ai_action_tester@example.com', 29, 'like'),
        ('ai_action_tester@example.com', 37, 'like'),
        ('ai_action_tester@example.com', 56, 'like'),
        ('ai_action_tester@example.com', 14, 'dislike'),
        ('ai_action_tester@example.com', 23, 'dislike'),
        ('ai_horror_tester@example.com', 13, 'like'),
        ('ai_horror_tester@example.com', 21, 'like'),
        ('ai_horror_tester@example.com', 52, 'like'),
        ('ai_horror_tester@example.com', 55, 'like'),
        ('ai_horror_tester@example.com', 9, 'dislike'),
        ('ai_horror_tester@example.com', 18, 'dislike'),
        ('ai_horror_tester@example.com', 57, 'dislike')
) AS seeded(email, movie_id, reaction)
    ON seeded.email = u.email;

INSERT INTO user_ratings (user_id, movie_id, rating, created_at)
SELECT u.user_id, seeded.movie_id, seeded.rating, NOW()
FROM users u
JOIN (
    VALUES
        ('ai_action_tester@example.com', 6, 8),
        ('ai_action_tester@example.com', 10, 9),
        ('ai_action_tester@example.com', 11, 9),
        ('ai_action_tester@example.com', 29, 8),
        ('ai_action_tester@example.com', 37, 9),
        ('ai_action_tester@example.com', 56, 10),
        ('ai_action_tester@example.com', 4, 3),
        ('ai_action_tester@example.com', 16, 2),
        ('ai_action_tester@example.com', 23, 2),
        ('ai_action_tester@example.com', 62, 3),
        ('ai_horror_tester@example.com', 13, 9),
        ('ai_horror_tester@example.com', 21, 8),
        ('ai_horror_tester@example.com', 52, 9),
        ('ai_horror_tester@example.com', 55, 8),
        ('ai_horror_tester@example.com', 14, 2),
        ('ai_horror_tester@example.com', 16, 3),
        ('ai_horror_tester@example.com', 59, 4),
        ('ai_horror_tester@example.com', 62, 3)
) AS seeded(email, movie_id, rating)
    ON seeded.email = u.email;

INSERT INTO watch_history (user_id, movie_id, watched_at, progress_percent)
SELECT u.user_id, seeded.movie_id, NOW(), seeded.progress_percent
FROM users u
JOIN (
    VALUES
        ('ai_action_tester@example.com', 6, 95),
        ('ai_action_tester@example.com', 10, 98),
        ('ai_action_tester@example.com', 11, 91),
        ('ai_action_tester@example.com', 29, 88),
        ('ai_action_tester@example.com', 37, 90),
        ('ai_action_tester@example.com', 56, 96),
        ('ai_action_tester@example.com', 59, 97),
        ('ai_horror_tester@example.com', 1, 93),
        ('ai_horror_tester@example.com', 13, 89),
        ('ai_horror_tester@example.com', 21, 84),
        ('ai_horror_tester@example.com', 38, 95),
        ('ai_horror_tester@example.com', 42, 91),
        ('ai_horror_tester@example.com', 52, 87),
        ('ai_horror_tester@example.com', 55, 90)
) AS seeded(email, movie_id, progress_percent)
    ON seeded.email = u.email;

COMMIT;
