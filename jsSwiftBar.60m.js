#!/usr/local/bin/node
/*
 * <xbar.title>jsSwiftBar</xbar.title>
 * <xbar.version>v1.0</xbar.version>
 * <xbar.author>hodgesd</xbar.author>
 * <xbar.author.github>hodgesd</xbar.author.github>
 * <xbar.desc>Test SwiftBar Plugin</xbar.desc>
 * <xbar.dependencies>node</xbar.dependencies>
 * <xbar.abouturl>http://varunmalhotra.xyz/blog/2016/02/bitbar-plugins-for-github-and-producthunt.html</xbar.abouturl>
 */

const jsdom = require('jsdom');
const { JSDOM } = jsdom;

url = 'https://www.techmeme.com/';

// Function to get the DOM of a webpage
function getDOM(url) {
  return JSDOM.fromURL(url);
}

console.log('TM' + '\n---\n');

getDOM(url).then((dom) => {
  // Find all the elements with the class "clus"
  const stories = dom.window.document.querySelectorAll('.clus');
  const storiesArray = Array.from(stories);
  //   console.log(storiesArray);
  // Loop through the stories and output the headline
  storiesArray.forEach((story) => {
    const storySite = story
      .querySelector('cite')
      .querySelector('a').textContent;
    const storyLink = story.querySelector('.ourh').href;
    const storyTitle = story.querySelector('.ourh').textContent;
    console.log(
      `${storyTitle.slice(0, 70)}...[${storySite}] | href= ${storyLink}`
    );
    // console.log(story.querySelector('.ourh').textContent);
  });
});
