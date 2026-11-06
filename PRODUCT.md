# Tactical RMM

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Technicians manage computers in Tactical RMM. The public support page serves people who have received an access code from a technician and need a Windows or Mac remote support tool.

## Product Purpose

Provide remote monitoring, management, and remote support through the existing Django API and MeshCentral integration.

## Capabilities and Constraints

- Public support downloads require a valid access code.
- Windows support installers use Azure Artifact Signing. Mac installers require Apple Developer ID signing and notarization.
- This repository contains the server; the Vue/Quasar dashboard ships from the separate tacticalrmm-web project.
- Working assumption for the support addition: use the existing MeshCentral transport. No separate ScreenConnect instance has been supplied.

## Brand Commitments

The support pages are white-label. The logo, company name and help contacts are set in the technician console, and the Tactical RMM name and logo are only the defaults. The pages use the JustUI kit: Geist type, neutral surfaces, one blue primary action, and light and dark themes that follow the visitor's system. Keep them as simple as possible, with one task per screen for the person being helped.
