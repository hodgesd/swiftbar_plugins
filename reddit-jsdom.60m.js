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

// # <swiftbar.hideAbout>true</swiftbar.hideAbout>
// # <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
// # <swiftbar.hideLastUpdated>true</swiftbar.hideLastUpdated>
// # <swiftbar.hideDisablePlugin>true</swiftbar.hideDisablePlugin>
// # <swiftbar.hideSwiftBar>true</swiftbar.hideSwiftBar>

const jsdom = require('jsdom');
const { JSDOM } = jsdom;

const url = 'https://www.reddit.com/';

async function getDOM(url) {
  return await JSDOM.fromURL(url);
}

getDOM(url).then((dom) => {
  // Get a reference to the Reddit homepage
  const homepage = dom.window.document;

  // Do something with the Reddit homepage
  console.log(homepage.title);

  // Find elements in the Reddit homepage
  const posts = homepage.querySelectorAll('._1oQyIsiPHYt6nx7VOmd1sz');
  console.log(posts.length + ' posts found');

  // Work with each post
  posts.forEach((post) => {
    const postTitle = post.querySelector('h3').textContent;
    console.log(postTitle);
  });
});
