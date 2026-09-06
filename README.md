# tjvz

Local, open-source, interpretable recommendation systems for focused reading.

Principles:

1. Privacy: All data is user-owned and doesn't leave your device unless you want it to.
2. Interpretability: You get to understand why an item is highly ranked in your feed. You can also manually set features like author reputation, publication reputation instead of having those learned from your feedback.

## Anatomy

1. Feed: You can follow two kinds of entities via RSS feeds: publications, and readers (who choose to broadcast their archives). The items in the feed are ordered according to the score computed by your local recommendation system.

2. Stack: You can promote items from the feed to the stack i.e. your reading list. 

3. Archive: A private list of things you have already read. Helps tjvz learn about your tastes and interests. You can optionally broadcast your reading archive via an RSS feed to share your reading with your friends.


## Recommendation systems

### Default

* Relevance (proximity to current)
* Publication reputation: 1 if subscribed 0 otherwise
* Author reputation: 1 if read something by them previously
* Reader recommendations: Number of people you follow who have read this



### Logistic regression (requires feedback):

When you either promote (to stack) or delete an item from your feed, tjvz updates the weights in order to try and predict what you like reading and what you don't.

Each item is scored by a weighted sum of:
* Relevance
* Publication reputation
* Author reputation
* Reader recommendations (i.e. how popular it is among the readers you follow)

The weights for each component are estimated by default using logistic regression, but you can set them to a fixed value yourself.
