# tjvz

A local, open-source, interpretable recommendation system for focused reading.

## Feed 

You can follow two kinds of entities via RSS feeds: publications, and readers (who choose to broadcast their archives). The items in the feed are ordered according to the score computed by your local recommendation system.

You can filter the feed by various parameters: length, format, genre.

## Stack

You can promote items from the feed to the stack i.e. your reading list. Once you mark something as read, it goes to your archive.

## Archive

A private list of things you have already read. Helps tjvz learn about your tastes and interests.

You can optionally broadcast your reading archive via an RSS feed to share your reading with your friends.

## Feedback

You can either promote (to stack) or delete an item from your feed.

## Scoring system

Each item is scored by a weighted sum of:
* Relevance
* Publication reputation
* Author reputation
* Recommendations (i.e. how popular it is among the readers you follow)

The weights for each component are estimated by default using logistic regression, but you can set them to a fixed value yourself.
